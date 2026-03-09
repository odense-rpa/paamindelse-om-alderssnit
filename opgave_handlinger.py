import logging

from datetime import date, datetime
from automation_server_client import WorkItemError
from dateutil.relativedelta import relativedelta
from kmd_nexus_client import NexusClientManager
from odk_tools.tracking import Tracker

logger = logging.getLogger(__name__)

PROCESS_NAME = "Påmindelse om alderssnit"


class OpgaveHandler:
    def __init__(self, nexus: NexusClientManager, tracker: Tracker):
        self.nexus = nexus
        self.tracker = tracker

    def opret_opgave_på_skema(
        self,
        borger: dict,
        fødselsdato: datetime,
        ansvarlig_medarbejder: dict,
        ansvarlig_org: dict,
        titel: str,
        måneds_offset: int,
    ):
        opgavefrist = fødselsdato + relativedelta(months=måneds_offset)
        visning = self.nexus.borgere.hent_visning(borger)
        aktiviteter = self.nexus.nexus_client.get(
            visning["_links"]["patientActivities"]["href"]
        ).json()
        form_activities = [
            a for a in aktiviteter if a.get("patientActivityType") == "formData"
        ]
        skema = None

        for activity in form_activities:
            form_title = activity.get("formDefinition", {}).get("title", "")
            if form_title == "Påmindelse om alderssnit":
                self_link = activity.get("_links", {}).get("self", {}).get("href", "")
                if self_link:
                    skema = self.nexus.nexus_client.get(self_link).json()
                break

        if skema is None:
            self.nexus.skemaer.opret_komplet_skema(
                borger=borger,
                skematype_navn="Påmindelse om alderssnit",
                handling_navn="Låst",
                data={},
            )
            visning = self.nexus.borgere.hent_visning(borger)
            aktiviteter = self.nexus.nexus_client.get(
                visning["_links"]["patientActivities"]["href"]
            ).json()
            form_activities = [
                a for a in aktiviteter if a.get("patientActivityType") == "formData"
            ]

            for activity in form_activities:
                form_title = activity.get("formDefinition", {}).get("title", "")
                if form_title == "Påmindelse om alderssnit":
                    self_link = (
                        activity.get("_links", {}).get("self", {}).get("href", "")
                    )
                    if self_link:
                        skema = self.nexus.nexus_client.get(self_link).json()
                    break

        if skema is None:
            raise WorkItemError("Kunne ikke finde eller oprette påmindelsesskema")

        historik = self.nexus.opgaver.hent_opgave_historik(skema)

        if any(o.get("title") == titel for o in historik):
            return

        self.nexus.opgaver.opret_opgave(
            objekt=skema,
            opgave_type="Påmindelse om alderssnit",
            titel=titel,
            ansvarlig_organisation=ansvarlig_org.get("name", ""),
            ansvarlig_medarbejder=ansvarlig_medarbejder,
            start_dato=date.today(),
            forfald_dato=opgavefrist,
        )
        self.tracker.track_task(PROCESS_NAME)

    def opret_opgave_på_skema_30(
        self,
        borger: dict,
        fødselsdato: datetime,
        ansvarlig_medarbejder: dict,
        ansvarlig_org: dict,
    ):
        """Create 30-year task on the newest 'Faglig vurdering' or 'Vurdering og formål' form."""
        titel = "Borger fylder 30 år"
        opgavefrist = fødselsdato + relativedelta(months=356)
        opgavetype = "Påmindelse om alderssnit - 30 år"
        skema_refs = self.nexus.skemaer.hent_skemareferencer(borger)

        if not skema_refs:
            return

        relevante = [
            ref
            for ref in skema_refs
            if ref.get("Navn", "").startswith("Faglig vurdering og indstilling")
            or ref.get("Navn", "").startswith("Vurdering og formål")
        ]

        if not relevante:
            return

        relevante.reverse()
        skema = self.nexus.skemaer.hent_skema_fra_reference(relevante[0])
        historik = self.nexus.opgaver.hent_opgave_historik(skema)

        if any(o.get("title") == titel for o in historik):
            return

        self.nexus.opgaver.opret_opgave(
            objekt=skema,
            opgave_type=opgavetype,
            titel=titel,
            ansvarlig_organisation=ansvarlig_org.get("name", ""),
            ansvarlig_medarbejder=ansvarlig_medarbejder,
            start_dato=date.today(),
            forfald_dato=opgavefrist,
        )
        self.tracker.track_partial_task(PROCESS_NAME)
