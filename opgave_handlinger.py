"""Task creation functions for age-milestone reminders in KMD Nexus.

Each function corresponds to a BP subsheet:
- opret_opgave_på_indsats      — 17.5 / 18 year tasks on BU grants
- opret_opgave_på_indsats_fsiii — 18 year tasks on FSIII grants
- opret_opgave_på_skema         — 15.5 / 17 year tasks on "Påmindelse om alderssnit" form
- opret_opgave_på_skema_30      — 30 year tasks on "Faglig vurdering" / "Vurdering og formål" form
"""

import logging
from datetime import date, datetime

from automation_server_client import WorkItemError
from dateutil.relativedelta import relativedelta
from kmd_nexus_client.tree_helpers import filter_by_path
from odk_tools.reporting import report

logger = logging.getLogger(__name__)

PROCESS_NAME = "Påmindelse om alderssnit"

# Workflow states that disqualify a grant from getting a task
SKIP_STATES = {"Afsluttet", "Annulleret", "Fjernet", "Frafaldet", "Fremtidigt ændret"}

# Age group → (task title, deadline month offset)
INDSATS_CONFIG = {
    "17.5": ("Borger fylder 17½ år", 209),
    "18": ("Borger fylder 18 år", 215),
}

SKEMA_CONFIG = {
    "15.5": ("Borger fylder 15.5 år", 192, "Påmindelse om alderssnit"),
    "17": ("Borger fylder 17 år", 210, "Påmindelse om alderssnit"),
}


def _get_nexus():
    """Import the module-level nexus client from main."""
    import main

    return main.nexus


# ---------------------------------------------------------------------------
# 17.5 / 18 — task on BU grant (indsats)
# ---------------------------------------------------------------------------

BU_INDSATS_FILTER = (
    "/Børn og Unge Grundforløb/patientPathwayReference/Indsatser/basketGrantReference"
)


def opret_opgave_på_indsats(
    borger: dict,
    fødselsdato: datetime,
    referencer,  # MWIN: Datatypen fra nexus objektet er helt i skoven....
    ansvarlig_medarbejder: dict,
    ansvarlig_org: dict,
    *,
    alderssnit: str,
):
    """Create task on BU grant for 17.5 or 18 year age milestone.

    For 17.5: only create if the grant section is § 46 or § 47.
    Skips grants with terminal workflow states.
    """
    nexus = _get_nexus()
    titel, month_offset = INDSATS_CONFIG[alderssnit]
    opgavefrist = fødselsdato + relativedelta(months=month_offset)

    indsats_refs = filter_by_path(referencer, BU_INDSATS_FILTER)

    created = False
    for ref in indsats_refs:
        indsats, værdier = nexus.indsatser.hent_indsats(ref)

        # Skip terminated grants
        state = indsats.get("workflowState", {}).get("name", "")
        if state in SKIP_STATES:
            continue

        # 17.5: only § 46 or § 47 sections
        if alderssnit == "17.5":
            section = (
                værdier.get("paragraph", {}).get("paragraph", {}).get("section", "")
            )
            if section not in ("§ 46", "§ 47"):
                continue

        # Duplicate check
        historik = nexus.opgaver.hent_opgave_historik(indsats)
        if any(o.get("title") == titel for o in historik):
            continue

        nexus.opgaver.opret_opgave(
            objekt=indsats,
            opgave_type="BU - påmindelse om alderssnit",
            titel=titel,
            ansvarlig_organisation=ansvarlig_org.get("name", ""),
            ansvarlig_medarbejder=ansvarlig_medarbejder,
            start_dato=date.today(),
            forfald_dato=opgavefrist,
        )
        created = True

        report(
            PROCESS_NAME,
            "Unge",
            {"Cpr": borger.get("business_key", ""), "Opgave": titel},
        )

    if not created and indsats_refs:
        logger.info(
            f"Ingen nye {alderssnit}-opgaver oprettet for {borger.get('business_key', '')}"
        )


# ---------------------------------------------------------------------------
# 18 — task on FSIII grant (Sundhedsfagligt grundforløb)
# ---------------------------------------------------------------------------

FSIII_INDSATS_FILTER = "/Sundhedsfagligt grundforløb/patientPathwayReference/Indsatser/basketGrantReference"


def opret_opgave_på_indsats_fsiii(
    borger: dict,
    fødselsdato: datetime,
    referencer,  # MWIN: Datatypen fra nexus objektet er helt i skoven....
    borgers_organisationer: list[dict],
    ansvarlig_medarbejder: dict,
    ansvarlig_org: dict,
):
    """Create 18-year task on FSIII grant, if citizen is in Hjælpemiddelservice."""
    nexus = _get_nexus()
    titel = "Borger fylder 18 år"
    opgavefrist = fødselsdato + relativedelta(months=215)

    # Only proceed if citizen is in Hjælpemiddelservice
    has_hms = any(
        o.get("name") == "Hjælpemiddelservice" for o in borgers_organisationer
    )
    if not has_hms:
        return

    indsats_refs = filter_by_path(referencer, FSIII_INDSATS_FILTER)
    if not indsats_refs:
        return

    for ref in indsats_refs:
        indsats, _ = nexus.indsatser.hent_indsats(ref)

        state = indsats.get("workflowState", {}).get("name", "")
        if state in SKIP_STATES:
            continue

        # Duplicate check
        historik = nexus.opgaver.hent_opgave_historik(indsats)
        if any(o.get("title") == titel for o in historik):
            continue

        nexus.opgaver.opret_opgave(
            objekt=indsats,
            opgave_type="BU - påmindelse om alderssnit",
            titel=titel,
            ansvarlig_organisation=ansvarlig_org.get("name", ""),
            ansvarlig_medarbejder=ansvarlig_medarbejder,
            start_dato=date.today(),
            forfald_dato=opgavefrist,
        )

        report(
            PROCESS_NAME,
            "Unge",
            {"Cpr": borger.get("business_key", ""), "Opgave": titel},
        )


# ---------------------------------------------------------------------------
# 15.5 / 17 — task on "Påmindelse om alderssnit" form (schema)
# ---------------------------------------------------------------------------


def opret_opgave_på_skema(
    borger: dict,
    fødselsdato: datetime,
    ansvarlig_medarbejder: dict,
    ansvarlig_org: dict,
    *,
    alderssnit: str,
):
    """Create task on the 'Påmindelse om alderssnit' form for 15.5 or 17 year milestone.

    If the form doesn't exist yet, it creates one with status 'Låst'.
    """
    nexus = _get_nexus()
    titel, month_offset, opgavetype = SKEMA_CONFIG[alderssnit]
    opgavefrist = fødselsdato + relativedelta(months=month_offset)

    # Get citizen activities and find the reminder form
    visning = nexus.borgere.hent_visning(borger)
    aktiviteter = nexus.nexus_client.get(visning["_links"]["patientActivities"]["href"]).json()

    form_activities = [
        a for a in aktiviteter if a.get("patientActivityType") == "formData"
    ]

    skema = None
    for activity in form_activities:
        form_title = activity.get("formDefinition", {}).get("title", "")
        if form_title == "Påmindelse om alderssnit":            
            self_link = activity.get("_links", {}).get("self", {}).get("href", "")
            if self_link:
                skema = nexus.nexus_client.get(self_link).json()
            break

    # Create the form if it doesn't exist    
    if skema is None:
        nexus.skemaer.opret_komplet_skema(
            borger=borger,
            skematype_navn="Påmindelse om alderssnit",
            handling_navn="Låst",
            data={}
        )
        # Re-fetch activities to get the new form
        visning = nexus.borgere.hent_visning(borger)
        aktiviteter = nexus.nexus_client.get(visning["_links"]["patientActivities"]["href"]).json()
        form_activities = [
            a for a in aktiviteter if a.get("patientActivityType") == "formData"
        ]
        for activity in form_activities:
            form_title = activity.get("formDefinition", {}).get("title", "")
            if form_title == "Påmindelse om alderssnit":
                self_link = activity.get("_links", {}).get("self", {}).get("href", "")
                if self_link:
                    skema = nexus.nexus_client.get(self_link).json()
                break

    if skema is None:
        raise WorkItemError("Kunne ikke finde eller oprette påmindelsesskema")

    # Duplicate check
    historik = nexus.opgaver.hent_opgave_historik(skema)
    if any(o.get("title") == titel for o in historik):
        return

    nexus.opgaver.opret_opgave(
        objekt=skema,
        opgave_type=opgavetype,
        titel=titel,
        ansvarlig_organisation=ansvarlig_org.get("name", ""),
        ansvarlig_medarbejder=ansvarlig_medarbejder,
        start_dato=date.today(),
        forfald_dato=opgavefrist,
    )

    report(
        PROCESS_NAME,
        "Unge",
        {"Cpr": borger.get("business_key", ""), "Opgave": titel},
    )


# ---------------------------------------------------------------------------
# 30 — task on "Faglig vurdering og indstilling" / "Vurdering og formål" form
# ---------------------------------------------------------------------------


def opret_opgave_på_skema_30(
    borger: dict,
    fødselsdato: datetime,
    ansvarlig_medarbejder: dict,
    ansvarlig_org: dict,
):
    """Create 30-year task on the newest 'Faglig vurdering' or 'Vurdering og formål' form."""
    nexus = _get_nexus()
    titel = "Borger fylder 30 år"
    opgavefrist = fødselsdato + relativedelta(months=356)
    opgavetype = "Påmindelse om alderssnit - 30 år"

    # Get schema references for the citizen
    skema_refs = nexus.skemaer.hent_skemareferencer(borger)
    if not skema_refs:
        return

    # Filter to the relevant form types
    relevante = [
        ref
        for ref in skema_refs
        if ref.get("Navn", "").startswith("Faglig vurdering og indstilling")
        or ref.get("Navn", "").startswith("Vurdering og formål")
    ]

    if not relevante:
        return

    # Use the last (newest) match — BP reverses the collection
    relevante.reverse()
    skema = nexus.skemaer.hent_skema_fra_reference(relevante[0])

    # Duplicate check
    historik = nexus.opgaver.hent_opgave_historik(skema)
    if any(o.get("title") == titel for o in historik):
        return

    nexus.opgaver.opret_opgave(
        objekt=skema,
        opgave_type=opgavetype,
        titel=titel,
        ansvarlig_organisation=ansvarlig_org.get("name", ""),
        ansvarlig_medarbejder=ansvarlig_medarbejder,
        start_dato=date.today(),
        forfald_dato=opgavefrist,
    )

    report(
        PROCESS_NAME,
        "Unge",
        {"Cpr": borger.get("business_key", ""), "Opgave": titel},
    )
