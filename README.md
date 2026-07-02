# Påmindelse om alderssnit

Automatisering der opretter opgaver i KMD Nexus til sagsbehandlere, når borgere nærmer sig kritiske alderssnit for overgang mellem ungdoms- og voksenydelser.

## Hvad gør robotten?

1. **Henter borgere** fra Nexus-databasen med 2 måneders forvarsel til alderssnit (15½, 17, 17½ og 18 år) samt 3½ måneder til §30-alderssnit (voksne)
2. **Finder ansvarlig medarbejder og organisation** via borgerens aktive grundforløb i Nexus
3. **Opretter en opgave** på et skema i Nexus med passende titel og frist
4. Borgere, hvor ansvarlig medarbejder ikke kan identificeres automatisk, sendes til **manuel behandling**

| Alderssnit | Forvarsel | Organisationer |
|---|---|---|
| 15½ år | 2 måneder | Ungerådgivningen, Familierådgivningen |
| 17 år | 2 måneder | Familierådgivningen |
| 17½ år | 2 måneder | Ungerådgivningen, Familierådgivningen — kun borgere med §46/47-indsatser |
| 18 år | 2 måneder | Ungerådgivningen, Familierådgivningen + hjælpemiddelservice ved aktive udlån |
| 30 år (§30) | 3½ måneder | Social og Jobrådgivningen, Specialenheden, Voksenrådgivningen |

## Forudsætninger

- Python ≥ 3.13
- [`uv`](https://docs.astral.sh/uv/) til pakkehåndtering
- Adgang til **Automation Server** (arbejdskø)
- Adgang til **KMD Nexus** (produktion)
- Adgang til **KMD Nexus-database** (SQL)
- En **Odense SQL Server**-konto til tracking

## Installation

```sh
uv sync
```

## Konfiguration

Credentials registreres i Automation Server:
- `KMD Nexus - produktion`
- `KMD Nexus - database`
- `Odense SQL Server`

## Kørsel

```sh
uv run python main.py --queue   # Fyld arbejdskøen
uv run python main.py           # Behandl arbejdskøen
```

## Afhængigheder

| Pakke | Formål |
|---|---|
| `automation-server-client` | Arbejdskø-håndtering |
| `kmd-nexus-client` | Integration med KMD Nexus |
| `nexus-database-client` | Opslag i Nexus SQL-database |
| `odk-tools` | Aktivitetssporing og rapportering |
| `python-dateutil` | Datohåndtering |

## GDPR og sikkerhed

Robotten behandler CPR-numre og oplysninger om borgeres sociale indsatser. Rapporten `paamindelse_om_alderssnit` indeholder CPR-numre for borgere der er sendt til manuel behandling — sikr at rapporten kun er tilgængelig for autoriserede medarbejdere.
