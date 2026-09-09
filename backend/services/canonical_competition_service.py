import os
import sys
import re
import unicodedata
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompetitionIdentity:
    """Canonical Identity for a Football Competition / League."""
    country: str
    country_code: str
    competition_name: str
    competition_code: str  # provider competition ID / code e.g. "ESPN-col.1"
    competition_display_name: str
    season: Optional[str] = None
    round_stage: Optional[str] = None
    provider: str = "ESPN"
    is_international: bool = False

    @property
    def country_name(self) -> str:
        """Backwards-compatibility property returning self.country."""
        return self.country

    @property
    def provider_competition_id(self) -> str:
        """Backwards-compatibility property returning self.competition_code."""
        return self.competition_code


class CanonicalCompetitionService:
    """
    Canonical Competition Identity & Metadata Service (Phase 13).
    Resolves competitions and countries accurately using verified provider metadata
    (provider feed code, provider country field, or verified competition name normalization).
    Strict Rule: NEVER infer country or competition from team names, team nationality,
    city names, or cached UI state. If unverified, returns 'UNAVAILABLE' / '—'.
    """

    # Direct ESPN Feed Code Mappings -> (canonical_competition_name, canonical_display_name, country, country_code, is_international)
    ESPN_CODE_MAP: Dict[str, Tuple[str, str, str, str, bool]] = {
        "eng.1": ("English Premier League", "Premier League", "England", "GB-ENG", False),
        "eng.2": ("English Championship", "Championship", "England", "GB-ENG", False),
        "eng.3": ("EFL League One", "League One", "England", "GB-ENG", False),
        "eng.4": ("EFL League Two", "League Two", "England", "GB-ENG", False),
        "eng.trophy": ("EFL Trophy", "EFL Trophy", "England", "GB-ENG", False),
        "eng.fa": ("English FA Cup", "FA Cup", "England", "GB-ENG", False),
        "eng.league_cup": ("Carabao Cup", "Carabao Cup", "England", "GB-ENG", False),
        "eng.w.1": ("Women's Super League", "Women's Super League", "England", "GB-ENG", False),
        "esp.1": ("Spanish LALIGA", "LaLiga", "Spain", "ES", False),
        "esp.2": ("Spanish LALIGA 2", "LaLiga 2", "Spain", "ES", False),
        "esp.copa_del_rey": ("Copa del Rey", "Copa del Rey", "Spain", "ES", False),
        "esp.w.1": ("Spanish Liga F", "Liga F", "Spain", "ES", False),
        "ita.1": ("Italian Serie A", "Serie A", "Italy", "IT", False),
        "ita.2": ("Italian Serie B", "Serie B", "Italy", "IT", False),
        "ita.coppa_italia": ("Coppa Italia", "Coppa Italia", "Italy", "IT", False),
        "ger.1": ("German Bundesliga", "Bundesliga", "Germany", "DE", False),
        "ger.2": ("German 2. Bundesliga", "2. Bundesliga", "Germany", "DE", False),
        "ger.dfb_pokal": ("DFB-Pokal", "DFB-Pokal", "Germany", "DE", False),
        "fra.1": ("French Ligue 1", "Ligue 1", "France", "FR", False),
        "fra.2": ("French Ligue 2", "Ligue 2", "France", "FR", False),
        "fra.coupe_de_france": ("Coupe de France", "Coupe de France", "France", "FR", False),
        "fra.w.1": ("French Première Ligue", "Première Ligue", "France", "FR", False),
        "bra.1": ("Brazilian Serie A", "Brasileirão Série A", "Brazil", "BR", False),
        "bra.2": ("Brazilian Serie B", "Brasileirão Série B", "Brazil", "BR", False),
        "bra.copa_do_brasil": ("Copa do Brasil", "Copa do Brasil", "Brazil", "BR", False),
        "arg.1": ("Argentine Liga Profesional", "Liga Profesional", "Argentina", "AR", False),
        "arg.2": ("Argentine Primera Nacional", "Primera Nacional", "Argentina", "AR", False),
        "arg.copa": ("Copa Argentina", "Copa Argentina", "Argentina", "AR", False),
        "sco.1": ("Scottish Premiership", "Scottish Premiership", "Scotland", "GB-SCT", False),
        "sco.2": ("SPFL Championship", "SPFL Championship", "Scotland", "GB-SCT", False),
        "sco.challenge_cup": ("SPFL Challenge Cup", "SPFL Challenge Cup", "Scotland", "GB-SCT", False),
        "sco.cup": ("Scottish Cup", "Scottish Cup", "Scotland", "GB-SCT", False),
        "sco.league_cup": ("Scottish League Cup", "Scottish League Cup", "Scotland", "GB-SCT", False),
        "ned.1": ("Dutch Eredivisie", "Eredivisie", "Netherlands", "NL", False),
        "ned.2": ("Dutch Eerste Divisie", "Eerste Divisie", "Netherlands", "NL", False),
        "ned.knvb_beker": ("KNVB Beker", "KNVB Beker", "Netherlands", "NL", False),
        "por.1": ("Portuguese Primeira Liga", "Primeira Liga", "Portugal", "PT", False),
        "por.taca": ("Taça de Portugal", "Taça de Portugal", "Portugal", "PT", False),
        "sau.1": ("Saudi Pro League", "Saudi Pro League", "Saudi Arabia", "SA", False),
        "tur.1": ("Turkish Super Lig", "Süper Lig", "Turkey", "TR", False),
        "bel.1": ("Belgian Pro League", "Pro League", "Belgium", "BE", False),
        "aut.1": ("Austrian Bundesliga", "Austrian Bundesliga", "Austria", "AT", False),
        "sui.1": ("Swiss Super League", "Swiss Super League", "Switzerland", "CH", False),
        "swe.1": ("Swedish Allsvenskan", "Allsvenskan", "Sweden", "SE", False),
        "nor.1": ("Norwegian Eliteserien", "Eliteserien", "Norway", "NO", False),
        "den.1": ("Danish Superliga", "Superliga", "Denmark", "DK", False),
        "mex.1": ("Mexican Liga MX", "Liga MX", "Mexico", "MX", False),
        "usa.1": ("Major League Soccer", "Major League Soccer", "USA", "US", False),
        "usa.usl": ("USL Championship", "USL Championship", "USA", "US", False),
        "usa.usl1": ("USL League One", "USL League One", "USA", "US", False),
        "usa.nwsl": ("NWSL", "NWSL", "USA", "US", False),
        "col.1": ("Colombian Primera A", "Primera A", "Colombia", "CO", False),
        "chi.1": ("Chilean Primera", "Primera División Chile", "Chile", "CL", False),
        "per.1": ("Peruvian Liga 1", "Liga 1", "Peru", "PE", False),
        "par.1": ("Paraguayan Primera", "Primera División Paraguay", "Paraguay", "PY", False),
        "ecu.1": ("LigaPro Ecuador", "LigaPro", "Ecuador", "EC", False),
        "bol.1": ("Bolivian Liga Profesional", "División Profesional", "Bolivia", "BO", False),
        "ven.1": ("Venezuelan Liga FUTVE", "Liga FUTVE", "Venezuela", "VE", False),
        "crc.1": ("Costa Rican Liga FPD", "Liga FPD", "Costa Rica", "CR", False),
        "hon.1": ("Honduran Liga Nacional", "Liga Nacional", "Honduras", "HN", False),
        "slv.1": ("Salvadoran Primera", "Primera División El Salvador", "El Salvador", "SV", False),
        "jpn.1": ("Japanese J1 League", "J1 League", "Japan", "JP", False),
        "gre.1": ("Greek Super League", "Super League Greece", "Greece", "GR", False),
        "rsa.1": ("South African Premier", "Premiership", "South Africa", "ZA", False),
        "can.1": ("Canadian Premier League", "Canadian Premier League", "Canada", "CA", False),
        "can.w.1": ("Northern Super League", "Northern Super League", "Canada", "CA", False),
        # International & Continental Tournaments
        "uefa.champions": ("UEFA Champions League", "UEFA Champions League", "Europe", "EU", True),
        "uefa.europa": ("UEFA Europa League", "UEFA Europa League", "Europe", "EU", True),
        "uefa.europa.conf": ("UEFA Conference League", "UEFA Conference League", "Europe", "EU", True),
        "uefa.super_cup": ("UEFA Super Cup", "UEFA Super Cup", "Europe", "EU", True),
        "uefa.nations": ("UEFA Nations League", "UEFA Nations League", "Europe", "EU", True),
        "uefa.w.champions": ("UEFA Women's Champions League", "UEFA Women's Champions League", "Europe", "EU", True),
        "uefa.w.europa": ("Women's Europa Cup", "Women's Europa Cup", "Europe", "EU", True),
        "copa.libertadores": ("CONMEBOL Libertadores", "CONMEBOL Libertadores", "South America", "CONMEBOL", True),
        "copa.sudamericana": ("CONMEBOL Sudamericana", "CONMEBOL Sudamericana", "South America", "CONMEBOL", True),
        "concacaf.champions": ("CONCACAF Champions Cup", "CONCACAF Champions Cup", "North America", "CONCACAF", True),
        "concacaf.central_american": ("Concacaf Central American Cup", "Concacaf Central American Cup", "North America", "CONCACAF", True),
        "concacaf.leagues_cup": ("Leagues Cup", "Leagues Cup", "North America", "CONCACAF", True),
        "caf.champions": ("CAF Champions League", "CAF Champions League", "Africa", "CAF", True),
        "caf.confederation": ("CAF Confederation Cup", "CAF Confederation Cup", "Africa", "CAF", True),
        "afc.champions_elite": ("AFC Champions League Elite", "AFC Champions League Elite", "Asia", "AFC", True),
        "afc.champions_two": ("AFC Champions League Two", "AFC Champions League Two", "Asia", "AFC", True),
        "fifa.world_cup": ("FIFA World Cup", "FIFA World Cup", "International", "INT", True),
        "fifa.club_world_cup": ("FIFA Club World Cup", "FIFA Club World Cup", "International", "INT", True),
        "global.friendlies": ("International Friendlies", "Friendlies", "International", "INT", True),
    }

    # ISO Country Normalization mapping (handles variations, translations, Unicode)
    COUNTRY_SYNONYMS: Dict[str, Tuple[str, str]] = {
        "england": ("England", "GB-ENG"),
        "great britain": ("United Kingdom", "GB"),
        "united kingdom": ("United Kingdom", "GB"),
        "uk": ("United Kingdom", "GB"),
        "scotland": ("Scotland", "GB-SCT"),
        "wales": ("Wales", "GB-WLS"),
        "northern ireland": ("Northern Ireland", "GB-NIR"),
        "spain": ("Spain", "ES"),
        "espana": ("Spain", "ES"),
        "españa": ("Spain", "ES"),
        "italy": ("Italy", "IT"),
        "italia": ("Italy", "IT"),
        "germany": ("Germany", "DE"),
        "deutschland": ("Germany", "DE"),
        "france": ("France", "FR"),
        "brazil": ("Brazil", "BR"),
        "brasil": ("Brazil", "BR"),
        "argentina": ("Argentina", "AR"),
        "netherlands": ("Netherlands", "NL"),
        "holland": ("Netherlands", "NL"),
        "portugal": ("Portugal", "PT"),
        "colombia": ("Colombia", "CO"),
        "chile": ("Chile", "CL"),
        "peru": ("Peru", "PE"),
        "perú": ("Peru", "PE"),
        "paraguay": ("Paraguay", "PY"),
        "ecuador": ("Ecuador", "EC"),
        "bolivia": ("Bolivia", "BO"),
        "venezuela": ("Venezuela", "VE"),
        "uruguay": ("Uruguay", "UY"),
        "mexico": ("Mexico", "MX"),
        "méxico": ("Mexico", "MX"),
        "usa": ("USA", "US"),
        "united states": ("USA", "US"),
        "canada": ("Canada", "CA"),
        "japan": ("Japan", "JP"),
        "nippon": ("Japan", "JP"),
        "saudi arabia": ("Saudi Arabia", "SA"),
        "turkey": ("Turkey", "TR"),
        "türkiye": ("Turkey", "TR"),
        "belgium": ("Belgium", "BE"),
        "austria": ("Austria", "AT"),
        "österreich": ("Austria", "AT"),
        "switzerland": ("Switzerland", "CH"),
        "schweiz": ("Switzerland", "CH"),
        "suisse": ("Switzerland", "CH"),
        "sweden": ("Sweden", "SE"),
        "sverige": ("Sweden", "SE"),
        "norway": ("Norway", "NO"),
        "norge": ("Norway", "NO"),
        "denmark": ("Denmark", "DK"),
        "danmark": ("Denmark", "DK"),
        "greece": ("Greece", "GR"),
        "south africa": ("South Africa", "ZA"),
        "costa rica": ("Costa Rica", "CR"),
        "honduras": ("Honduras", "HN"),
        "el salvador": ("El Salvador", "SV"),
        "guatemala": ("Guatemala", "GT"),
        "china": ("China", "CN"),
        "russia": ("Russia", "RU"),
        "europe": ("Europe", "EU"),
        "south america": ("South America", "CONMEBOL"),
        "north america": ("North America", "CONCACAF"),
        "africa": ("Africa", "CAF"),
        "asia": ("Asia", "AFC"),
        "international": ("International", "INT"),
    }

    # Strict regex pattern rules for competition name / note matching.
    # Order: specific regional tournaments BEFORE generic league titles
    # Format: (regex_pattern, canonical_country, country_code, canonical_competition_name, display_name, is_international)
    PATTERN_RULES = [
        # 1. Continental & International Tournaments
        (r"\b(uefa women'?s champions league|women'?s champions league)\b", "Europe", "EU", "UEFA Women's Champions League", "UEFA Women's Champions League", True),
        (r"\b(uefa champions league)\b", "Europe", "EU", "UEFA Champions League", "UEFA Champions League", True),
        (r"\b(uefa europa league)\b", "Europe", "EU", "UEFA Europa League", "UEFA Europa League", True),
        (r"\b(uefa conference league|uecl)\b", "Europe", "EU", "UEFA Conference League", "UEFA Conference League", True),
        (r"\b(uefa nations league)\b", "Europe", "EU", "UEFA Nations League", "UEFA Nations League", True),
        (r"\b(women'?s europa cup)\b", "Europe", "EU", "Women's Europa Cup", "Women's Europa Cup", True),
        (r"\b(conmebol libertadores|copa libertadores)\b", "South America", "CONMEBOL", "CONMEBOL Libertadores", "CONMEBOL Libertadores", True),
        (r"\b(conmebol sudamericana|copa sudamericana)\b", "South America", "CONMEBOL", "CONMEBOL Sudamericana", "CONMEBOL Sudamericana", True),
        (r"\b(concacaf central american cup)\b", "North America", "CONCACAF", "Concacaf Central American Cup", "Concacaf Central American Cup", True),
        (r"\b(concacaf champions|concacaf)\b", "North America", "CONCACAF", "CONCACAF Champions Cup", "CONCACAF Champions Cup", True),
        (r"\b(leagues cup)\b", "North America", "CONCACAF", "Leagues Cup", "Leagues Cup", True),
        (r"\b(caf champions league)\b", "Africa", "CAF", "CAF Champions League", "CAF Champions League", True),
        (r"\b(caf confederation cup|women'?s africa cup of nations|africa cup of nations)\b", "Africa", "CAF", "CAF Confederation Cup", "CAF Confederation Cup", True),
        (r"\b(afc champions league elite|afc champions league two|afc champions league|afc asian cup|asean champ)\b", "Asia", "AFC", "AFC Champions League", "AFC Champions League", True),
        (r"\b(fifa club world cup)\b", "International", "INT", "FIFA Club World Cup", "FIFA Club World Cup", True),
        (r"\b(fifa world cup)\b", "International", "INT", "FIFA World Cup", "FIFA World Cup", True),
        (r"\b(international friendl(y|ies)|club friendl(y|ies)|friendly match)\b", "International", "INT", "International Friendlies", "Friendlies", True),

        # 2. Collegiate / Youth Competitions (Must resolve to USA before domestic generic patterns)
        (r"\b(ncaaw|ncaa\s*w(omen)?)\b", "USA", "US", "NCAAW Soccer", "NCAAW Soccer", False),
        (r"\b(ncaam|ncaa\s*m(en)?)\b", "USA", "US", "NCAAM Soccer", "NCAAM Soccer", False),
        (r"\b(ncaa|college soccer|collegiate)\b", "USA", "US", "NCAA Soccer", "NCAA Soccer", False),

        # 3. Country-Specific Tournaments & Distinct League Names
        # Scotland (Must precede generic 'Premier League')
        (r"\b(scottish premiership|spfl premiership)\b", "Scotland", "GB-SCT", "Scottish Premiership", "Scottish Premiership", False),
        (r"\b(spfl championship)\b", "Scotland", "GB-SCT", "SPFL Championship", "SPFL Championship", False),
        (r"\b(spfl challenge cup)\b", "Scotland", "GB-SCT", "SPFL Challenge Cup", "SPFL Challenge Cup", False),
        (r"\b(scottish cup)\b", "Scotland", "GB-SCT", "Scottish Cup", "Scottish Cup", False),
        (r"\b(scottish league cup)\b", "Scotland", "GB-SCT", "Scottish League Cup", "Scottish League Cup", False),
        (r"\b(scottish|spfl)\b", "Scotland", "GB-SCT", "Scottish Football", "Scottish Football", False),

        # England
        (r"\b(english premier league|premier league|epl)\b", "England", "GB-ENG", "English Premier League", "Premier League", False),
        (r"\b(english championship|efl championship)\b", "England", "GB-ENG", "English Championship", "Championship", False),
        (r"\b(efl league one|league one)\b", "England", "GB-ENG", "EFL League One", "League One", False),
        (r"\b(efl league two|league two)\b", "England", "GB-ENG", "EFL League Two", "League Two", False),
        (r"\b(efl trophy)\b", "England", "GB-ENG", "EFL Trophy", "EFL Trophy", False),
        (r"\b(carabao cup|english league cup)\b", "England", "GB-ENG", "Carabao Cup", "Carabao Cup", False),
        (r"\b(english fa cup|fa cup|fa trophy)\b", "England", "GB-ENG", "English FA Cup", "FA Cup", False),
        (r"\b(women'?s super league|wsl)\b", "England", "GB-ENG", "Women's Super League", "Women's Super League", False),
        (r"\b(national league|community shield)\b", "England", "GB-ENG", "English National League", "National League", False),

        # Spain
        (r"\b(laliga 2|la liga 2|segunda divisi[oó]n|laliga hypermotion)\b", "Spain", "ES", "Spanish LALIGA 2", "LaLiga 2", False),
        (r"\b(laliga|la liga|primera divisi[oó]n sp(ain)?|laliga ea sports)\b", "Spain", "ES", "Spanish LALIGA", "LaLiga", False),
        (r"\b(copa del rey)\b", "Spain", "ES", "Copa del Rey", "Copa del Rey", False),
        (r"\b(copa de la reina|liga f)\b", "Spain", "ES", "Spanish Liga F", "Liga F", False),
        (r"\b(supercopa de espa[ñn]a|trofeo joan gamper)\b", "Spain", "ES", "Supercopa de España", "Supercopa", False),

        # Italy
        (r"\b(serie a enilive|serie a|italian serie a)\b", "Italy", "IT", "Italian Serie A", "Serie A", False),
        (r"\b(serie bkt|serie b|italian serie b)\b", "Italy", "IT", "Italian Serie B", "Serie B", False),
        (r"\b(coppa italia|supercoppa italiana)\b", "Italy", "IT", "Coppa Italia", "Coppa Italia", False),

        # Germany
        (r"\b(2\.?\s*bundesliga|german 2\.?\s*bundesliga)\b", "Germany", "DE", "German 2. Bundesliga", "2. Bundesliga", False),
        (r"\b(bundesliga|german bundesliga)\b", "Germany", "DE", "German Bundesliga", "Bundesliga", False),
        (r"\b(dfb-pokal|dfb pokal|german cup|german supercup)\b", "Germany", "DE", "DFB-Pokal", "DFB-Pokal", False),

        # France
        (r"\b(ligue 1|french ligue 1|ligue 1 mcdonald'?s)\b", "France", "FR", "French Ligue 1", "Ligue 1", False),
        (r"\b(ligue 2|french ligue 2)\b", "France", "FR", "French Ligue 2", "Ligue 2", False),
        (r"\b(coupe de france|premi[eè]re ligue|trophee des champions)\b", "France", "FR", "Coupe de France", "Coupe de France", False),

        # Netherlands
        (r"\b(eredivisie|dutch eredivisie)\b", "Netherlands", "NL", "Dutch Eredivisie", "Eredivisie", False),
        (r"\b(eerste divisie|keuken kampioen)\b", "Netherlands", "NL", "Dutch Eerste Divisie", "Eerste Divisie", False),
        (r"\b(knvb beker|knvb)\b", "Netherlands", "NL", "KNVB Beker", "KNVB Beker", False),

        # Portugal
        (r"\b(primeira liga|liga portugal|portuguese primeira liga)\b", "Portugal", "PT", "Portuguese Primeira Liga", "Primeira Liga", False),
        (r"\b(ta[cç]a de portugal|ta[cç]a da liga)\b", "Portugal", "PT", "Taça de Portugal", "Taça de Portugal", False),

        # Brazil
        (r"\b(brasileir[aã]o|brazilian serie a|s[ée]rie a brazil)\b", "Brazil", "BR", "Brazilian Serie A", "Brasileirão Série A", False),
        (r"\b(brazilian serie b|s[ée]rie b brazil)\b", "Brazil", "BR", "Brazilian Serie B", "Brasileirão Série B", False),
        (r"\b(copa do bra[sz]il|paulista|carioca)\b", "Brazil", "BR", "Copa do Brasil", "Copa do Brasil", False),

        # Argentina
        (r"\b(argentine liga profesional|torneo betano|lpf|liga profesional de f[uú]tbol|liga profesional argentina)\b", "Argentina", "AR", "Argentine Liga Profesional", "Liga Profesional", False),
        (r"\b(primera nacional|nacional b argentina)\b", "Argentina", "AR", "Argentine Primera Nacional", "Primera Nacional", False),
        (r"\b(copa argentina|primera b metro)\b", "Argentina", "AR", "Copa Argentina", "Copa Argentina", False),

        # Colombia
        (r"\b(colombian primera a|primera a colombia|liga betplay|copa colombia)\b", "Colombia", "CO", "Colombian Primera A", "Primera A", False),

        # Mexico
        (r"\b(liga mx|liga bbva mx|copa mx|expansi[oó]n mx|mexican liga mx)\b", "Mexico", "MX", "Mexican Liga MX", "Liga MX", False),

        # USA
        (r"\b(major league soccer|mls)\b", "USA", "US", "Major League Soccer", "Major League Soccer", False),
        (r"\b(usl championship|usl league one|usl)\b", "USA", "US", "USL Championship", "USL Championship", False),
        (r"\b(nwsl)\b", "USA", "US", "NWSL", "NWSL", False),

        # Austria
        (r"\b(admiral bundesliga|austrian bundesliga|austria bundesliga)\b", "Austria", "AT", "Austrian Bundesliga", "Austrian Bundesliga", False),

        # Switzerland
        (r"\b(credit suisse super league|swiss super league)\b", "Switzerland", "CH", "Swiss Super League", "Swiss Super League", False),

        # Saudi Arabia
        (r"\b(saudi pro league|roshn saudi league|king'?s cup)\b", "Saudi Arabia", "SA", "Saudi Pro League", "Saudi Pro League", False),

        # Turkey
        (r"\b(turkish super lig|s[uü]per lig|turkey cup)\b", "Turkey", "TR", "Turkish Super Lig", "Süper Lig", False),

        # Belgium
        (r"\b(belgian pro league|jupiler pro league|belgian cup)\b", "Belgium", "BE", "Belgian Pro League", "Pro League", False),

        # Sweden
        (r"\b(allsvenskan|superettan|svenska cupen)\b", "Sweden", "SE", "Swedish Allsvenskan", "Allsvenskan", False),

        # Norway
        (r"\b(eliteserien|obos-ligaen|nm cup)\b", "Norway", "NO", "Norwegian Eliteserien", "Eliteserien", False),

        # Denmark
        (r"\b(danish superliga|superligaen|dbu pokalen)\b", "Denmark", "DK", "Danish Superliga", "Superliga", False),

        # South Africa
        (r"\b(south african premier|psl|betway premiership|nedbank cup)\b", "South Africa", "ZA", "South African Premier", "Premiership", False),

        # Greece
        (r"\b(greek super league|super league greece|greek cup)\b", "Greece", "GR", "Greek Super League", "Super League Greece", False),

        # Japan
        (r"\b(japanese j1 league|j1 league|j2 league|j-league)\b", "Japan", "JP", "Japanese J1 League", "J1 League", False),

        # Ecuador
        (r"\b(ecuadorian ligapro|ligapro ecuador|ligapro|copa ecuador)\b", "Ecuador", "EC", "LigaPro Ecuador", "LigaPro", False),

        # Chile
        (r"\b(chilean primera|campeonato nacional chile|copa chile)\b", "Chile", "CL", "Chilean Primera", "Primera División Chile", False),

        # Peru
        (r"\b(peruvian liga 1|liga 1 peru|copa bicentenario)\b", "Peru", "PE", "Peruvian Liga 1", "Liga 1", False),

        # Uruguay
        (r"\b(uruguayan primera division|campeonato uruguayo|liga auf)\b", "Uruguay", "UY", "Uruguayan Primera Division", "Primera División Uruguay", False),

        # Bolivia
        (r"\b(bolivian liga profesional|bolivian primera division|divisi[oó]n profesional bolivia|copa bolivia)\b", "Bolivia", "BO", "Bolivian Liga Profesional", "División Profesional", False),

        # Venezuela
        (r"\b(venezuelan liga futve|liga futve|primera divisi[oó]n venezuela)\b", "Venezuela", "VE", "Venezuelan Liga FUTVE", "Liga FUTVE", False),

        # Costa Rica
        (r"\b(liga fpd|costa rican liga fpd)\b", "Costa Rica", "CR", "Costa Rican Liga FPD", "Liga FPD", False),

        # Honduras
        (r"\b(honduran liga nacional|liga nacional honduras)\b", "Honduras", "HN", "Honduran Liga Nacional", "Liga Nacional", False),

        # Guatemala
        (r"\b(guatemalan liga nacional|guatemalan|liga nacional guatemala|liga guate)\b", "Guatemala", "GT", "Guatemalan Liga Nacional", "Liga Nacional", False),

        # El Salvador
        (r"\b(salvadoran primera division|salvadoran|primera division el salvador|liga mayor el salvador)\b", "El Salvador", "SV", "Salvadoran Primera Division", "Primera División El Salvador", False),

        # Paraguay
        (r"\b(paraguayan primera division|paraguayan|primera division paraguay|copa paraguay)\b", "Paraguay", "PY", "Paraguayan Primera Division", "Primera División Paraguay", False),

        # El Salvador
        (r"\b(salvadoran primera|primera divisi[oó]n el salvador)\b", "El Salvador", "SV", "Salvadoran Primera", "Primera División El Salvador", False),

        # Canada
        (r"\b(canadian premier league|northern super league)\b", "Canada", "CA", "Canadian Premier League", "Canadian Premier League", False),
    ]

    @staticmethod
    def _normalize_string(val: Optional[str]) -> str:
        """Strip accents and whitespace for safe comparison."""
        if not val:
            return ""
        n = unicodedata.normalize('NFKD', str(val)).encode('ASCII', 'ignore').decode('utf-8')
        return n.strip().lower()

    @classmethod
    def resolve_competition(
        cls,
        provider_code: Optional[str] = None,
        league_name: Optional[str] = None,
        season_slug: Optional[str] = None,
        alt_note: Optional[str] = None,
        notes: Optional[str] = None,
        home_team_name: Optional[str] = None,
        away_team_name: Optional[str] = None,
        provided_country: Optional[str] = None,
        provided_season: Optional[str] = None,
        provided_round: Optional[str] = None,
    ) -> CompetitionIdentity:
        """
        Derives canonical fixture competition & country identity.
        MANDATORY RULES:
        1. Never infer country or competition from team names, team nationality, city names,
           frontend grouping, or previous fixture state.
        2. Preserves provider competition code & name as provenance.
        3. Normalizes alternate provider competition names, abbreviations, translated names,
           and Unicode variations.
        4. If unverified, outputs 'UNAVAILABLE' / '—' without guessing.
        """
        code_key = (provider_code or "").strip().lower()

        # Step 1: Direct ESPN Provider Code Resolution (Authoritative)
        if code_key and code_key != "all" and code_key in cls.ESPN_CODE_MAP:
            c_name, c_disp, country, c_code, is_intl = cls.ESPN_CODE_MAP[code_key]
            # Use alt_note or league_name if cleanly available, else canonical
            display_name = c_disp
            canonical_name = c_name
            return CompetitionIdentity(
                country=country,
                country_code=c_code,
                competition_name=canonical_name,
                competition_code=f"ESPN-{code_key}",
                competition_display_name=display_name,
                season=provided_season or season_slug,
                round_stage=provided_round or (alt_note if alt_note and alt_note != display_name else None),
                provider="ESPN",
                is_international=is_intl
            )

        # Step 2: Check provided_country metadata if supplied by provider
        norm_provided_country = cls._normalize_string(provided_country)
        canonical_prov_country = None
        canonical_prov_code = None
        if norm_provided_country in cls.COUNTRY_SYNONYMS:
            canonical_prov_country, canonical_prov_code = cls.COUNTRY_SYNONYMS[norm_provided_country]

        # Step 3: Match competition / alt_note against authoritative pattern rules
        # Exclude generic stage strings
        generic_stages = [
            "regular-season", "league-phase", "group-stage", "first-round", "second-round",
            "third-round", "quarterfinals", "semifinals", "final", "finals", "playoffs"
        ]
        filtered_league_name = league_name if (league_name and league_name.lower() not in ["global matches & cup competitions", "all"]) else None
        
        # Test candidate texts in order of specificity: alt_note, filtered_league_name, notes
        search_candidates = [c for c in [alt_note, filtered_league_name, notes] if c and c.strip()]
        for cand in search_candidates:
            clean_cand = cand.strip()
            for pattern, c_country, c_code, c_name, c_disp, is_intl in cls.PATTERN_RULES:
                if re.search(pattern, clean_cand, re.IGNORECASE):
                    # Derive stable slug ID
                    clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', c_disp.lower()).strip('-')
                    prov_id = f"ESPN-{code_key}" if (code_key and code_key != "all") else f"COMP-{clean_slug}"
                    return CompetitionIdentity(
                        country=c_country,
                        country_code=c_code,
                        competition_name=c_name,
                        competition_code=prov_id,
                        competition_display_name=c_disp,
                        season=provided_season or season_slug,
                        round_stage=provided_round or (alt_note if alt_note != cand else None),
                        provider="ESPN",
                        is_international=is_intl
                    )

        # Step 4: If provider explicitly provided country metadata, combine with clean competition name
        raw_comp_name = (alt_note or filtered_league_name or notes or "").strip()
        if canonical_prov_country and raw_comp_name:
            clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', raw_comp_name.lower()).strip('-')
            prov_id = f"ESPN-{code_key}" if (code_key and code_key != "all") else f"COMP-{clean_slug}"
            return CompetitionIdentity(
                country=canonical_prov_country,
                country_code=canonical_prov_code or "—",
                competition_name=raw_comp_name,
                competition_code=prov_id,
                competition_display_name=raw_comp_name,
                season=provided_season or season_slug,
                round_stage=provided_round,
                provider="ESPN",
                is_international=False
            )

        # Step 5: If raw competition name contains international / friendly keywords
        if raw_comp_name and re.search(r'\b(friendly|friendlies|international)\b', raw_comp_name, re.IGNORECASE):
            clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', raw_comp_name.lower()).strip('-')
            return CompetitionIdentity(
                country="International",
                country_code="INT",
                competition_name=raw_comp_name,
                competition_code=f"COMP-{clean_slug}",
                competition_display_name=raw_comp_name,
                season=provided_season or season_slug,
                round_stage=provided_round,
                provider="ESPN",
                is_international=True
            )

        # Step 6: Mandatory Phase 13 Fallback: Honest UNAVAILABLE (No Guessing, No Fabrication)
        # If competition name is completely missing or generic
        final_comp_name = raw_comp_name if raw_comp_name else "UNAVAILABLE"
        clean_slug = re.sub(r'[^a-zA-Z0-9]+', '-', final_comp_name.lower()).strip('-')
        return CompetitionIdentity(
            country="UNAVAILABLE",
            country_code="—",
            competition_name=final_comp_name,
            competition_code=f"COMP-{clean_slug or 'unknown'}",
            competition_display_name=final_comp_name,
            season=provided_season or season_slug,
            round_stage=provided_round,
            provider="ESPN",
            is_international=False
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
        Returns ('UNAVAILABLE', '—') if unmapped and not international.
        """
        if not league_name:
            return ("UNAVAILABLE", "—")
        ident = cls.resolve_competition(league_name=league_name)
        return (ident.country, ident.country_code)

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
