"""Påmindelse om alderssnit — reminds case workers about age milestones.

Identifies citizens approaching 15.5, 17, 17.5, 18, or 30 years of age
and creates reminder tasks in KMD Nexus for their case workers.
"""

import asyncio
import logging
import sys
from typing import List

from automation_server_client import (
    AutomationServer,
    Credential,
    WorkItemError,
    WorkItemStatus,
)
from dateutil.parser import parse as parse_date
from kmd_nexus_client import NexusClientManager
from nexus_database_client import NexusDatabaseClient
from odk_tools.tracking import Tracker

from opgave_handlinger import (
    opret_opgave_på_indsats,
    opret_opgave_på_indsats_fsiii,
    opret_opgave_på_skema,
    opret_opgave_på_skema_30,
)

logger = logging.getLogger(__name__)

PROCESS_NAME = "Påmindelse om alderssnit"

# Module-level globals — initialized in __main__
nexus: NexusClientManager
db: NexusDatabaseClient
tracker: Tracker


def populate_queue(workqueue):
    """Run 5 DB queries and add citizens to the work queue, tagged by age group."""
    queries = [
        (db.hent_borgere_med_2_måneder_til_15_5, "15.5"),
        (db.hent_borgere_med_2_måneder_til_17, "17"),
        (db.hent_borgere_med_2_måneder_til_17_5, "17.5"),
        (db.hent_borgere_med_2_måneder_til_18, "18"),
        (db.hent_borgere_med_3_5_måneder_til_30, "30"),
    ]

    for query_fn, alderssnit in queries:
        borgere = query_fn()
        logger.info(f"Fandt {len(borgere)} borgere til {alderssnit}-års alderssnit")

        for borger in borgere:
            workqueue.add(
                {
                    "Cpr": borger["Cpr"],
                    "Fødselsdato": borger["birth_date"],
                    "Alderssnit": alderssnit,
                },
                reference=borger["Cpr"],
            )


async def process_workqueue(workqueue):
    """Process each work item: dispatch to the correct task-creation function."""
    for item in workqueue:
        with item:
            data = item.data

            try:
                cpr = data["Cpr"]
                fødselsdato = parse_date(data["Fødselsdato"], dayfirst=True)
                alderssnit = data["Alderssnit"]

                # Look up citizen and pathway in Nexus
                borger = nexus.borgere.hent_borger(cpr)

                if not borger:
                    raise WorkItemError("Borger ikke fundet")

                visning = nexus.borgere.hent_visning(borger) or {}
                referencer = nexus.borgere.hent_referencer(visning) or {}

                borgers_organisationer =nexus.organisationer.hent_organisationer_for_borger(borger);

                # Find responsible worker and organization
                ansvarlig_medarbejder, ansvarlig_org = _find_organisation(
                    borger,
                    borgers_organisationer,
                    referencer,
                )

                # Dispatch based on age group
                if alderssnit == "17.5":
                    opret_opgave_på_indsats(
                        borger,
                        fødselsdato,
                        referencer,
                        ansvarlig_medarbejder,
                        ansvarlig_org,
                        alderssnit="17.5",
                    )
                elif alderssnit == "18":
                    opret_opgave_på_indsats(
                        borger,
                        fødselsdato,
                        referencer,
                        ansvarlig_medarbejder,
                        ansvarlig_org,
                        alderssnit="18",
                    )
                    opret_opgave_på_indsats_fsiii(
                        borger,
                        fødselsdato,
                        referencer,
                        borgers_organisationer,
                        ansvarlig_medarbejder,
                        ansvarlig_org,
                    )

                if alderssnit in ("15.5", "17"):
                    opret_opgave_på_skema(
                        borger,
                        fødselsdato,
                        ansvarlig_medarbejder,
                        ansvarlig_org,
                        alderssnit=alderssnit,
                    )

                if alderssnit == "30":
                    opret_opgave_på_skema_30(
                        borger,
                        fødselsdato,
                        ansvarlig_medarbejder,
                        ansvarlig_org,
                    )

                tracker.track_task(PROCESS_NAME)

            except WorkItemError as e:
                logger.error(f"Fejl ved behandling af {data.get('Cpr', '?')}: {e}")
                item.fail(str(e))


# ---------------------------------------------------------------------------
# Organization resolution
# ---------------------------------------------------------------------------

GODKENDTE_ORGANISATIONER = [
    "Ungerådgivningen 1\n",
    "Ungerådgivningen 2\n",
    "Ungerådgivningen 3",
    "Ungerådgivningen Special - Kompensation",
    "Familierådgivningen",
    "Ungerådgivningen Social 1 - Vagt & Visitation",
]

MEDARBEJDER_FILTER = "/Børn og Unge Grundforløb/professionalReference"


def _find_organisation(
    borger: dict,
    borgers_organisationer: list[dict],
    referencer,
) -> tuple[str, str]:
    """Find the responsible worker and their approved organization.

    Mirrors the BP "Find fælles org" subsheet logic:
    1. Filter references to find the professional on the BU pathway.
    2. Look up the professional's organizations.
    3. Match against the approved organization list AND the citizen's orgs.
    """
    from kmd_nexus_client.tree_helpers import filter_by_path

    # Find professionals assigned to the citizen's BU pathway
    medarbejder_refs = filter_by_path(referencer, MEDARBEJDER_FILTER)

    if not medarbejder_refs:
        return "", ""

    # Use first matched professional
    # MIWN - forskel imellem de to objekter - TODO: Implementer hent organisationer for bruger - hører til organisationsclient
    bruger = nexus.hent_fra_reference(medarbejder_refs[0])
    medarbejders_organisationer = []
    #bruger = nexus.organisationer.hent_bruger_via_reference(medarbejder_refs[0])
    #medarbejders_organisationer = nexus.organisationer.hent_brugers_organisationer(
    #    bruger
    #)

    # Find a common approved organization
    for org_navn in GODKENDTE_ORGANISATIONER:
        org_stripped = org_navn.strip()
        medarbejder_match = any(
            o.get("name") == org_stripped for o in medarbejders_organisationer
        )
        borger_match = any(
            o.get("name") == org_stripped for o in borgers_organisationer
        )
        if medarbejder_match and borger_match:
            return bruger.get("primaryIdentifier", ""), org_stripped

    return "", ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    nexus_cred = Credential.get_credential("KMD Nexus - produktion")
    db_cred = Credential.get_credential("KMD Nexus - database")
    tracking_cred = Credential.get_credential("Odense SQL Server")

    nexus = NexusClientManager(
        instance=nexus_cred.data["instance"],
        client_id=nexus_cred.username,
        client_secret=nexus_cred.password,
    )

    db = NexusDatabaseClient(
        host=db_cred.data.get("host", "195.50.52.160"),
        port=int(db_cred.data.get("port", "2311")),
        user=db_cred.username,
        password=db_cred.password,
        database=db_cred.data.get("database", "odensekommune"),
    )

    tracker = Tracker(tracking_cred.username, tracking_cred.password)

    if "--queue" in sys.argv:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        populate_queue(workqueue)
        db.close()
        sys.exit(0)

    asyncio.run(process_workqueue(workqueue))
    db.close()
