import asyncio
import logging
import sys

from automation_server_client import (
    AutomationServer,
    Credential,
    WorkItemStatus,
)
from dateutil.parser import parse as parse_date
from kmd_nexus_client import NexusClientManager
from kmd_nexus_client.tree_helpers import filter_by_path
from nexus_database_client import NexusDatabaseClient
from odk_tools.tracking import Tracker
from odk_tools.reporting import report
from opgave_handlinger import OpgaveHandler


GODKENDTE_ORGANISATIONER_UNGE = [
    "Ungerådgivningen 1",
    "Ungerådgivningen 2",
    "Ungerådgivningen 3",
    "Ungerådgivningen Special - Kompensation",
    "Familierådgivningen",
    "Ungerådgivningen Social 1 - Vagt & Visitation",
]

SYTTEN_AAR_ORGANISATIONER = ["Familierådgivningen"]

GODKENDTE_ORGANISATIONER_VOKSNE = [
    "Social og Jobrådgivningen",
    "Specialenheden",
    "Voksenrådgivningen",
]

SKEMA_CONFIG = {
    "15.5": ("Borger fylder 15.5 år", 192),
    "17": ("Borger fylder 17 år", 210),
    "17.5": ("Borger fylder 17½ år", 209),
    "18": ("Borger fylder 18 år", 215),
}

GRUNDFORLØB_MEDARBEJDER_FILTER = "/Børn og Unge Grundforløb/professionalReference"
FORLØB_MEDARBEJDER_FILTER = "/Børn og Unge Grundforløb/*/professionalReference"

HJÆLPEMIDDEL_MEDARBEJDER_FILTER_1 = "/Ældre og sundhedsfagligt grundforløb/Sag Kropsbårne hjælpemidler - Børn og unge/professionalReference"
HJÆLPEMIDDEL_MEDARBEJDER_FILTER_2 = "/Ældre og sundhedsfagligt grundforløb/Sag: Hjælpemidler, forbrugsgoder, boligindretning m.v. - Børn og unge/professionalReference"

SOCIALFAGLIGT_MEDARBEJDER_FILTER_1 = "/Socialfagligt grundforløb/professionalReference"
SOCIALFAGLIGT_MEDARBEJDER_FILTER_2 = "/Socialfagligt grundforløb/*/professionalReference"

nexus: NexusClientManager
nexus_database: NexusDatabaseClient
tracker: Tracker
opgave_handler: OpgaveHandler
logger = logging.getLogger(__name__)


def _find_organisation(
    referencer, godkendte_organisationer: list[str], type=""
) -> tuple[dict, dict] | None:    
    
    if type == "Hjælpemidler":
        medarbejder_refs = filter_by_path(referencer, HJÆLPEMIDDEL_MEDARBEJDER_FILTER_1)
        if not medarbejder_refs:
            medarbejder_refs = filter_by_path(
                referencer, HJÆLPEMIDDEL_MEDARBEJDER_FILTER_2
            )
            if not medarbejder_refs:
                return None
    elif type == "30":
        medarbejder_refs = filter_by_path(referencer, SOCIALFAGLIGT_MEDARBEJDER_FILTER_1)
        if not medarbejder_refs:
            medarbejder_refs = filter_by_path(
                referencer, SOCIALFAGLIGT_MEDARBEJDER_FILTER_2
            )
            if not medarbejder_refs:
                return None
    else:
        medarbejder_refs = filter_by_path(referencer, GRUNDFORLØB_MEDARBEJDER_FILTER)

        if not medarbejder_refs:
            medarbejder_refs = filter_by_path(referencer, FORLØB_MEDARBEJDER_FILTER)
            if not medarbejder_refs:
                return None

    bruger = nexus.hent_fra_reference(medarbejder_refs[0])
    medarbejder = nexus_database.hent_medarbejder_med_activity_id(
        bruger.get("activityIdentifier", {}).get("activityId", "")
    )
    medarbejder = nexus.organisationer.hent_medarbejder_ved_initialer(
        medarbejder[0].get("primary_identifier", "")
    )

    additional_info = bruger.get("additionalInfo", [])
    primary_org_name = None
    for info in additional_info:
        if info.get("key") == "Primær organisation":
            value = info.get("value")
            if value and isinstance(value, str) and value.strip():
                primary_org_name = value.strip()
                break
    if primary_org_name and primary_org_name in godkendte_organisationer:
        organisation = nexus.organisationer.hent_organisation_ved_navn(primary_org_name)
        return medarbejder, organisation
    return None


def populate_queue(workqueue):
    """Run 5 DB queries and add citizens to the work queue, tagged by age group."""
    queries = [
        (nexus_database.hent_borgere_med_2_måneder_til_15_5, "15.5"),
        (nexus_database.hent_borgere_med_2_måneder_til_17, "17"),
        (nexus_database.hent_borgere_med_2_måneder_til_17_5, "17.5"),
        (nexus_database.hent_borgere_med_2_måneder_til_18, "18"),
        (nexus_database.hent_borgere_med_3_5_måneder_til_30, "30"),
    ]

    for query_fn, alderssnit in queries:
        borgere = query_fn()
        logger.info(f"Fandt {len(borgere)} borgere til {alderssnit}-års alderssnit")

        for borger in borgere:
            workqueue.add_item(
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
                borger = nexus.borgere.hent_borger(cpr)

                if not borger:
                    raise Exception("Borger ikke fundet")

                visning = nexus.borgere.hent_visning(borger)
                referencer = nexus.borgere.hent_referencer(visning)

                if alderssnit == "17":
                    godkendte_organisationer = SYTTEN_AAR_ORGANISATIONER
                elif alderssnit == "30":
                    godkendte_organisationer = GODKENDTE_ORGANISATIONER_VOKSNE
                else:
                    godkendte_organisationer = GODKENDTE_ORGANISATIONER_UNGE

                result = _find_organisation(referencer, godkendte_organisationer, type="30" if alderssnit=="30" else "")
                if not result:
                    raise Exception(
                        "Ansvarlig medarbejder eller organisation ikke fundet"
                    )
                ansvarlig_medarbejder, ansvarlig_org = result

                if alderssnit in ("15.5", "17", "18"):
                    # 18 årige, skal både have opgave på Børn og Unge grundforløb og Ældre og sundhedsfagligt grundforløb, hvis de har aktive udlån.
                    opgave_handler.opret_opgave_på_skema(
                        borger,
                        fødselsdato,
                        ansvarlig_medarbejder,
                        ansvarlig_org,                        
                        titel=SKEMA_CONFIG[alderssnit][0],
                        måneds_offset=SKEMA_CONFIG[alderssnit][1],
                    )
                if alderssnit == "17.5":
                    indsatser = opgave_handler.hent_indsater_paragraf_46_47(referencer)

                    if len(indsatser) == 0:
                        continue

                    opgave_handler.opret_opgave_på_skema(
                        borger,
                        fødselsdato,
                        ansvarlig_medarbejder,
                        ansvarlig_org,                        
                        titel=SKEMA_CONFIG[alderssnit][0],
                        måneds_offset=SKEMA_CONFIG[alderssnit][1],
                    )
                
                if alderssnit == "18":
                    udlån = nexus.borgere.hent_udlån(borger)

                    if udlån is None or len(udlån) == 0:
                        continue

                    result = _find_organisation(
                        referencer, godkendte_organisationer, type="Hjælpemidler"
                    )
                    if not result:
                        raise Exception(
                            "Ansvarlig medarbejder ift. Hjælpemiddelservice ikke fundet"
                        )
                    ansvarlig_medarbejder, ansvarlig_org = result                    

                    opgave_handler.opret_opgave_på_skema(
                        borger,
                        fødselsdato,
                        ansvarlig_medarbejder,
                        ansvarlig_org,                        
                        titel="Borger fylder 18 år - Hjælpemiddel",
                        måneds_offset=SKEMA_CONFIG[alderssnit][1],
                    )
                if alderssnit == "30":
                    opgave_handler.opret_opgave_på_skema_30(
                        borger,
                        fødselsdato,
                        ansvarlig_medarbejder,
                        ansvarlig_org,
                    )

            except Exception as e:
                if alderssnit == "30":
                    report(
                        "paamindelse_om_alderssnit",
                        "Voksne",
                        {"Cpr": data.get("Cpr", ""), "Manuel årsag": str(e)},
                    )
                else:
                    report(
                        "paamindelse_om_alderssnit",
                        "Unge",
                        {"Cpr": data.get("Cpr", ""), "Manuel årsag": str(e)},
                    )
                logger.error(f"Fejl ved behandling af {data.get('Cpr', '?')}: {e}")
                item.fail(str(e))


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

    nexus_database = NexusDatabaseClient(
        host=db_cred.data["hostname"],
        port=int(db_cred.data["port"]),
        user=db_cred.username,
        password=db_cred.password,
        database=db_cred.data["database_name"],
    )

    tracker = Tracker(tracking_cred.username, tracking_cred.password)

    opgave_handler = OpgaveHandler(nexus, tracker)

    if "--queue" in sys.argv:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        populate_queue(workqueue)
        nexus_database.close()
        sys.exit(0)

    asyncio.run(process_workqueue(workqueue))
    nexus_database.close()
