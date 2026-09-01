import os
import sys
import re
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompetitionIdentity:
    """Canonical Identity for a Football Competition / League."""
    provider: str
    provider_competition_id: str
    competition_name: str
    country_name: str
    country_code: str
    is_international: bool = False


class CanonicalCompetitionService:
    """
    Canonical Competition Identity & Metadata Service.
    Resolves competitions and countries accurately using provider external codes
    and conservative pattern matching, preventing country fabrication or collision.
    """

    # Direct ESPN Feed Code Mappings
    ESPN_CODE_MAP = {
        "eng.1": ("English Premier League", "England", "GB-ENG", False),
        "eng.2": ("English Championship", "England", "GB-ENG", False),
        "eng.3": ("EFL League One", "England", "GB-ENG", False),
        "eng.4": ("EFL League Two", "England", "GB-ENG", False),
        "eng.trophy": ("EFL Trophy", "England", "GB-ENG", False),
        "eng.fa": ("English FA Cup", "England", "GB-ENG", False),
        "eng.league_cup": ("Carabao Cup", "England", "GB-ENG", False),
        "eng.w.1": ("Women's Super League", "England", "GB-ENG", False),
        "esp.1": ("Spanish LALIGA", "Spain", "ES", False),
        "esp.2": ("Spanish LALIGA 2", "Spain", "ES", False),
        "esp.copa_del_rey": ("Copa del Rey", "Spain", "ES", False),
        "esp.w.1": ("Spanish Liga F", "Spain", "ES", False),
        "ita.1": ("Italian Serie A", "Italy", "IT", False),
        "ita.2": ("Italian Serie B", "Italy", "IT", False),
        "ita.coppa_italia": ("Coppa Italia", "Italy", "IT", False),
        "ger.1": ("German Bundesliga", "Germany", "DE", False),
        "ger.2": ("German 2. Bundesliga", "Germany", "DE", False),
        "ger.dfb_pokal": ("DFB-Pokal", "Germany", "DE", False),
        "fra.1": ("French Ligue 1", "France", "FR", False),
        "fra.2": ("French Ligue 2", "France", "FR", False),
        "fra.coupe_de_france": ("Coupe de France", "France", "FR", False),
        "fra.w.1": ("French Première Ligue", "France", "FR", False),
        "bra.1": ("Brazilian Serie A", "Brazil", "BR", False),
        "bra.2": ("Brazilian Serie B", "Brazil", "BR", False),
        "bra.copa_do_brasil": ("Copa do Brasil", "Brazil", "BR", False),
        "arg.1": ("Argentine Liga Profesional", "Argentina", "AR", False),
        "arg.2": ("Argentine Primera Nacional", "Argentina", "AR", False),
        "arg.copa": ("Copa Argentina", "Argentina", "AR", False),
        "sco.1": ("Scottish Premiership", "Scotland", "GB-SCT", False),
        "sco.2": ("SPFL Championship", "Scotland", "GB-SCT", False),
        "sco.challenge_cup": ("SPFL Challenge Cup", "Scotland", "GB-SCT", False),
        "sco.cup": ("Scottish Cup", "Scotland", "GB-SCT", False),
        "sco.league_cup": ("Scottish League Cup", "Scotland", "GB-SCT", False),
        "ned.1": ("Dutch Eredivisie", "Netherlands", "NL", False),
        "ned.2": ("Dutch Eerste Divisie", "Netherlands", "NL", False),
        "ned.knvb_beker": ("KNVB Beker", "Netherlands", "NL", False),
        "por.1": ("Portuguese Primeira Liga", "Portugal", "PT", False),
        "por.taca": ("Taça de Portugal", "Portugal", "PT", False),
        "sau.1": ("Saudi Pro League", "Saudi Arabia", "SA", False),
        "tur.1": ("Turkish Super Lig", "Turkey", "TR", False),
        "bel.1": ("Belgian Pro League", "Belgium", "BE", False),
        "aut.1": ("Austrian Bundesliga", "Austria", "AT", False),
        "sui.1": ("Swiss Super League", "Switzerland", "CH", False),
        "swe.1": ("Swedish Allsvenskan", "Sweden", "SE", False),
        "nor.1": ("Norwegian Eliteserien", "Norway", "NO", False),
        "den.1": ("Danish Superliga", "Denmark", "DK", False),
        "mex.1": ("Liga MX", "Mexico", "MX", False),
        "usa.1": ("Major League Soccer", "USA", "US", False),
        "usa.usl": ("USL Championship", "USA", "US", False),
        "usa.usl1": ("USL League One", "USA", "US", False),
        "usa.nwsl": ("NWSL", "USA", "US", False),
        "col.1": ("Colombian Primera A", "Colombia", "CO", False),
        "chi.1": ("Chilean Primera", "Chile", "CL", False),
        "per.1": ("Peru Liga 1", "Peru", "PE", False),
        "par.1": ("Paraguayan Primera", "Paraguay", "PY", False),
        "ecu.1": ("LigaPro Ecuador", "Ecuador", "EC", False),
        "bol.1": ("Bolivian Liga Profesional", "Bolivia", "BO", False),
        "ven.1": ("Liga FUTVE", "Venezuela", "VE", False),
        "crc.1": ("Liga FPD", "Costa Rica", "CR", False),
        "hon.1": ("Honduran Liga Nacional", "Honduras", "HN", False),
        "slv.1": ("Salvadoran Primera", "El Salvador", "SV", False),
        "jpn.1": ("Japanese J1 League", "Japan", "JP", False),
        "gre.1": ("Greek Super League", "Greece", "GR", False),
        "rsa.1": ("South African Premier", "South Africa", "ZA", False),
        "can.1": ("Canadian Premier League", "Canada", "CA", False),
        "can.w.1": ("Northern Super League", "Canada", "CA", False),
        # International & Continental Club / Country Competitions
        "uefa.champions": ("UEFA Champions League", "Europe", "EU", True),
        "uefa.europa": ("UEFA Europa League", "Europe", "EU", True),
        "uefa.europa.conf": ("UEFA Conference League", "Europe", "EU", True),
        "uefa.super_cup": ("UEFA Super Cup", "Europe", "EU", True),
        "uefa.nations": ("UEFA Nations League", "Europe", "EU", True),
        "uefa.w.champions": ("UEFA Women's Champions League", "Europe", "EU", True),
        "uefa.w.europa": ("Women's Europa Cup", "Europe", "EU", True),
        "copa.libertadores": ("CONMEBOL Libertadores", "South America", "CONMEBOL", True),
        "copa.sudamericana": ("CONMEBOL Sudamericana", "South America", "CONMEBOL", True),
        "concacaf.champions": ("CONCACAF Champions Cup", "North America", "CONCACAF", True),
        "concacaf.central_american": ("Concacaf Central American Cup", "North America", "CONCACAF", True),
        "concacaf.leagues_cup": ("Leagues Cup", "North America", "CONCACAF", True),
        "caf.champions": ("CAF Champions League", "Africa", "CAF", True),
        "caf.confederation": ("CAF Confederation Cup", "Africa", "CAF", True),
        "afc.champions_elite": ("AFC Champions League Elite", "Asia", "AFC", True),
        "afc.champions_two": ("AFC Champions League Two", "Asia", "AFC", True),
        "fifa.world_cup": ("FIFA World Cup", "International", "INT", True),
        "fifa.club_world_cup": ("FIFA Club World Cup", "International", "INT", True),
        "global.friendlies": ("International Friendlies", "International", "INT", True),
    }

    # Conservative Name Pattern Rules (Order: Specific country/continental prefixes BEFORE generic single-word league titles)
    PATTERN_RULES = [
        # 1. Explicit Continental Tournaments
        (r"\b(uefa women'?s champions league|women'?s champions league)\b", "Europe", "EU"),
        (r"\b(uefa champions league)\b", "Europe", "EU"),
        (r"\b(uefa europa league)\b", "Europe", "EU"),
        (r"\b(uefa conference league|uecl)\b", "Europe", "EU"),
        (r"\b(uefa nations league)\b", "Europe", "EU"),
        (r"\b(women'?s europa cup)\b", "Europe", "EU"),
        (r"\b(conmebol libertadores|copa libertadores)\b", "South America", "CONMEBOL"),
        (r"\b(conmebol sudamericana|copa sudamericana)\b", "South America", "CONMEBOL"),
        (r"\b(concacaf central american cup)\b", "North America", "CONCACAF"),
        (r"\b(concacaf champions|concacaf)\b", "North America", "CONCACAF"),
        (r"\b(leagues cup)\b", "North America", "CONCACAF"),
        (r"\b(caf champions league)\b", "Africa", "CAF"),
        (r"\b(caf confederation cup|women'?s africa cup of nations|africa cup of nations)\b", "Africa", "CAF"),
        (r"\b(afc champions league elite|afc champions league two|afc champions league|afc asian cup|asean champ)\b", "Asia", "AFC"),
        (r"\b(fifa club world cup|fifa world cup)\b", "International", "INT"),

        # 2. Country-Specific Prefixes & Unique Tournaments
        # Scotland
        (r"\b(scottish|spfl|scotland)\b", "Scotland", "GB-SCT"),
        
        # England & UK
        (r"\b(english|efl|carabao cup|fa cup|fa trophy|community shield|women'?s super league|wsl|national league)\b", "England", "GB-ENG"),
        
        # Austria (Before German Bundesliga)
        (r"\b(austrian|austria|admiral bundesliga)\b", "Austria", "AT"),
        
        # Switzerland (Before German/French leagues)
        (r"\b(swiss|switzerland|credit suisse)\b", "Switzerland", "CH"),

        # Brazil (Before generic Serie A / Serie B)
        (r"\b(brazilian|brazil|copa do bra[sz]il|paulista|carioca|brasileir[aã]o)\b", "Brazil", "BR"),
        
        # Argentina
        (r"\b(argentine|argentina|copa argentina|nacional b|primera b metro|torneo betano|lpf)\b", "Argentina", "AR"),
        
        # Bolivia
        (r"\b(bolivian|bolivia|copa bolivia)\b", "Bolivia", "BO"),
        
        # Colombia
        (r"\b(colombian|colombia|copa colombia|liga betplay)\b", "Colombia", "CO"),
        
        # Peru
        (r"\b(peruvian|peru|liga 1 peru|copa bicentenario)\b", "Peru", "PE"),
        
        # Paraguay
        (r"\b(paraguayan|paraguay|copa paraguay)\b", "Paraguay", "PY"),
        
        # Ecuador
        (r"\b(ecuadorian|ecuador|ligapro|copa ecuador)\b", "Ecuador", "EC"),
        
        # Chile
        (r"\b(chilean|chile|copa chile)\b", "Chile", "CL"),
        
        # Uruguay
        (r"\b(uruguay|uruguaya|liga auf)\b", "Uruguay", "UY"),
        
        # Guatemala
        (r"\b(guatemalan|guatemala)\b", "Guatemala", "GT"),
        
        # Mexico
        (r"\b(liga mx|liga bbva mx|copa mx|expansi[oó]n mx|mexican|mexico)\b", "Mexico", "MX"),
        
        # USA & North America
        (r"\b(major league soccer|mls|usl|nwsl)\b", "USA", "US"),
        
        # Canada
        (r"\b(canadian premier|northern super league|canada)\b", "Canada", "CA"),
        
        # Costa Rica
        (r"\b(liga fpd|costa rica)\b", "Costa Rica", "CR"),
        
        # Honduras
        (r"\b(honduran|honduras)\b", "Honduras", "HN"),
        
        # El Salvador
        (r"\b(salvadoran|el salvador)\b", "El Salvador", "SV"),
        
        # Venezuela
        (r"\b(liga futve|venezuela)\b", "Venezuela", "VE"),
        
        # Japan
        (r"\b(j1 league|j2 league|j-league|japanese|japan)\b", "Japan", "JP"),
        
        # China
        (r"\b(chinese super league|csl|china)\b", "China", "CN"),
        
        # Russia
        (r"\b(russian premier|russia)\b", "Russia", "RU"),
        
        # Greece
        (r"\b(greek super league|greek cup|super league greece|greece)\b", "Greece", "GR"),
        
        # Turkey
        (r"\b(turkish super lig|super lig|turkey cup|turkey)\b", "Turkey", "TR"),
        
        # Saudi Arabia
        (r"\b(saudi pro league|king'?s cup|saudi)\b", "Saudi Arabia", "SA"),
        
        # Belgium
        (r"\b(belgian pro league|jupiler pro|belgian cup|belgium)\b", "Belgium", "BE"),
        
        # Sweden
        (r"\b(allsvenskan|superettan|svenska cupen|sweden|swedish)\b", "Sweden", "SE"),
        
        # Norway
        (r"\b(eliteserien|obos-ligaen|nm cup|norway|norwegian)\b", "Norway", "NO"),
        
        # Denmark
        (r"\b(danish superliga|superligaen|dbu pokalen|denmark|danish)\b", "Denmark", "DK"),
        
        # South Africa
        (r"\b(south african premier|psl|betway premiership|nedbank cup|south africa)\b", "South Africa", "ZA"),

        # Spain
        (r"\b(laliga|la liga|copa del rey|copa de la reina|supercopa de espana|liga f|trofeo joan gamper|spain|spanish)\b", "Spain", "ES"),
        
        # Italy
        (r"\b(serie a|serie b|coppa italia|supercoppa italiana|italy|italian)\b", "Italy", "IT"),
        
        # Germany
        (r"\b(bundesliga|2\. bundesliga|dfb-pokal|german cup|german supercup|franz beckenbauer|germany|german)\b", "Germany", "DE"),
        
        # France
        (r"\b(ligue 1|ligue 2|coupe de france|premi[eè]re ligue|trophee des champions|france|french)\b", "France", "FR"),
        
        # Netherlands
        (r"\b(eredivisie|eerste divisie|knvb beker|knvb|keuken kampioen|netherlands|dutch)\b", "Netherlands", "NL"),
        
        # Portugal
        (r"\b(primeira liga|liga portugal|ta[cç]a de portugal|ta[cç]a da liga|portugal|portuguese)\b", "Portugal", "PT"),

        # General English Leagues (Checked after specific countries)
        (r"\b(premier league|championship|league one|league two)\b", "England", "GB-ENG"),

        # Friendlies / General
        (r"\b(friendly|friendlies|club friendly)\b", "International", "INT"),
    ]

    @classmethod
    def resolve_competition(
        cls,
        provider_code: Optional[str] = None,
        league_name: Optional[str] = None,
        season_slug: Optional[str] = None,
        alt_note: Optional[str] = None,
        notes: Optional[str] = None
    ) -> CompetitionIdentity:
        """
        Derives canonical competition name, country name, and country code.
        Precedence:
        1. Stable provider feed code (if specific, e.g. 'eng.1', 'esp.1', 'ita.1').
        2. Clean competition name and notes matching against authoritative pattern rules.
        3. Safe fallback ('Unknown Competition' / 'International'). Never fabricates a country.
        """
        code_key = (provider_code or "").strip().lower()

        # 1. Direct provider code match (when not generic 'all')
        if code_key and code_key != "all" and code_key in cls.ESPN_CODE_MAP:
            c_name, country, c_code, is_intl = cls.ESPN_CODE_MAP[code_key]
            # Use specific league name if available, else standard canonical name
            clean_name = (league_name or c_name).strip()
            return CompetitionIdentity(
                provider="ESPN",
                provider_competition_id=f"ESPN-{code_key}",
                competition_name=clean_name or c_name,
                country_name=country,
                country_code=c_code,
                is_international=is_intl
            )

        # 2. Derive from available display titles and notes
        candidate_text = " ".join(filter(None, [
            alt_note,
            league_name,
            notes,
            season_slug
        ])).strip()

        if not candidate_text:
            return CompetitionIdentity(
                provider="ESPN",
                provider_competition_id="ESPN-unknown",
                competition_name="Unknown Competition",
                country_name="International",
                country_code="INT",
                is_international=True
            )

        # Resolve primary competition display name
        display_name = (alt_note or league_name or notes or "Unknown Competition").strip()
        
        # Check against authoritative pattern rules
        for pattern, country, c_code in cls.PATTERN_RULES:
            if re.search(pattern, candidate_text, re.IGNORECASE):
                clean_id_slug = re.sub(r'[^a-zA-Z0-9]+', '-', display_name.lower()).strip('-')
                is_intl = country in ["Europe", "South America", "North America", "Africa", "Asia", "International"]
                return CompetitionIdentity(
                    provider="ESPN",
                    provider_competition_id=f"ESPN-all-{clean_id_slug}",
                    competition_name=display_name,
                    country_name=country,
                    country_code=c_code,
                    is_international=is_intl
                )

        # 3. Safe fallback if unmapped: Honest display, no fake country
        clean_id_slug = re.sub(r'[^a-zA-Z0-9]+', '-', display_name.lower()).strip('-')
        return CompetitionIdentity(
            provider="ESPN",
            provider_competition_id=f"ESPN-all-{clean_id_slug or 'unknown'}",
            competition_name=display_name,
            country_name="International",
            country_code="INT",
            is_international=True
        )

    SCHOOL_YOUTH_COMPETITION_KEYWORDS = [
        "ncaa", "ncaam", "ncaaw", "college", "collegiate", "high school", "middle school",
        "prep school", "varsity", "bucs", "isssa", "inter-school", "boys soccer", "girls soccer",
        "u-17", "u-18", "u-19", "u-20", "u-21", "u-23", "u17", "u18", "u19", "u20", "u21", "u23",
        "under-17", "under-18", "under-19", "under-20", "under-21", "under-23",
        "youth league", "sub-17", "sub-18", "sub-19", "sub-20", "sub-21", "sub-23",
        "sub 17", "sub 18", "sub 19", "sub 20", "sub 21", "sub 23", "juvenil", "student league"
    ]

    PROFESSIONAL_CLUB_EXEMPTIONS = [
        "universidad catolica", "universidad de chile", "universidad de concepcion",
        "universitario de deportes", "universitario de vinto", "tecnico universitario",
        "universidad san martin", "universidad cesar vallejo", "pumas unam",
        "academia puerto cabello", "academico de viseu", "hamilton academical",
        "universitario"
    ]

    @classmethod
    def get_country_for_league_name(cls, league_name: Optional[str]) -> Tuple[str, str]:
        """
        Convenience helper to resolve (country_name, country_code) for any league name.
        """
        if not league_name:
            return ("International", "INT")
        ident = cls.resolve_competition(league_name=league_name)
        return (ident.country_name, ident.country_code)

    @classmethod
    def is_school_or_youth_competition(
        cls,
        league_name: Optional[str] = "",
        provider_code: Optional[str] = "",
        home_team_name: Optional[str] = "",
        away_team_name: Optional[str] = "",
        notes: Optional[str] = ""
    ) -> bool:
        """
        Authoritative validator checking if a competition, team matchup, or feed
        belongs to school, college, collegiate, high school, youth, or U17-U23 categories.
        """
        combined_comp = f"{league_name or ''} {provider_code or ''} {notes or ''}".lower()
        for kw in cls.SCHOOL_YOUTH_COMPETITION_KEYWORDS:
            if kw in combined_comp:
                return True

        for team in [home_team_name, away_team_name]:
            if not team:
                continue
            t_low = team.lower().strip()
            
            # Allow established senior professional clubs
            if any(exc in t_low for exc in cls.PROFESSIONAL_CLUB_EXEMPTIONS):
                continue
            
            # Check for U17 - U23 youth academy squad indicators
            if re.search(r'\b(u|under|sub)[ -]?(17|18|19|20|21|23)\b', t_low, re.IGNORECASE):
                return True
            
            # Check for school, college, university, prep, or state varsity mascot keywords
            for kw in [
                "university", "college", "high school", "prep school", "varsity",
                "state buckeyes", "state bulldogs", "state spartans", "state seminoles",
                "state wildcats", "state nittany lions", "state bears", "state cyclones",
                "state owls", "state hornets", "state rams", "state gamecocks",
                "state panthers", "state bison", "state redbirds", "state mountaineers"
            ]:
                if kw in t_low:
                    return True

        return False
