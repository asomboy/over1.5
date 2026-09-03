import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from database import Base, get_db
import models
from services.canonical_competition_service import CanonicalCompetitionService, CompetitionIdentity
from services.ingestion_service import DataIngestionService
from main import app


class TestCanonicalCompetition(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.TestingSessionLocal()

        def override_get_db():
            try:
                yield self.db
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        app.dependency_overrides.clear()

    def test_provider_code_resolution(self):
        """Test resolution of standard league codes directly to their canonical country identity."""
        cases = [
            ("eng.1", "English Premier League", "England", "GB-ENG", False),
            ("eng.2", "English Championship", "England", "GB-ENG", False),
            ("esp.1", "Spanish LALIGA", "Spain", "ES", False),
            ("ita.1", "Italian Serie A", "Italy", "IT", False),
            ("ger.1", "German Bundesliga", "Germany", "DE", False),
            ("fra.1", "French Ligue 1", "France", "FR", False),
            ("bra.1", "Brazilian Serie A", "Brazil", "BR", False),
            ("arg.1", "Argentine Liga Profesional", "Argentina", "AR", False),
            ("sco.1", "Scottish Premiership", "Scotland", "GB-SCT", False),
            ("ned.1", "Dutch Eredivisie", "Netherlands", "NL", False),
            ("por.1", "Portuguese Primeira Liga", "Portugal", "PT", False),
            ("mex.1", "Mexican Liga BBVA MX", "Mexico", "MX", False),
            ("usa.1", "Major League Soccer", "USA", "US", False),
            ("uefa.champions", "UEFA Champions League", "Europe", "EU", True),
            ("copa.libertadores", "Copa Libertadores", "South America", "CONMEBOL", True),
        ]
        for code, expected_name_substr, exp_country, exp_code, exp_intl in cases:
            ident = CanonicalCompetitionService.resolve_competition(provider_code=code)
            self.assertEqual(ident.country_name, exp_country, f"Failed for code {code}: got {ident.country_name}, expected {exp_country}")
            self.assertEqual(ident.country_code, exp_code, f"Failed code {code}: got {ident.country_code}, expected {exp_code}")
            self.assertEqual(ident.is_international, exp_intl, f"Failed intl {code}: got {ident.is_international}")

    def test_pattern_disambiguation_rules(self):
        """Test name-based pattern matching with strictly ordered precedence."""
        cases = [
            ("Scottish Premiership", ("Scotland", "GB-SCT")),
            ("Scottish League Cup", ("Scotland", "GB-SCT")),
            ("English Premier League", ("England", "GB-ENG")),
            ("EFL Trophy", ("England", "GB-ENG")),
            ("Coppa Italia", ("Italy", "IT")),
            ("Italian Serie B", ("Italy", "IT")),
            ("DFB-Pokal", ("Germany", "DE")),
            ("Coupe de France", ("France", "FR")),
            ("Copa del Rey", ("Spain", "ES")),
            ("Copa Bolivia", ("Bolivia", "BO")),
            ("Bolivian Primera Division", ("Bolivia", "BO")),
            ("Copa Sudamericana", ("South America", "CONMEBOL")),
            ("CONCACAF Champions Cup", ("North America", "CONCACAF")),
            ("AFC Champions League", ("Asia", "AFC")),
            ("CAF Champions League", ("Africa", "CAF")),
        ]
        for name, (exp_country, exp_code) in cases:
            country, code = CanonicalCompetitionService.get_country_for_league_name(name)
            self.assertEqual(country, exp_country, f"Failed for {name}: got {country}, expected {exp_country}")
            self.assertEqual(code, exp_code, f"Failed for {name}: got {code}, expected {exp_code}")

    def test_unmapped_fallback_zero_fabrication(self):
        """Ensure unmapped or ambiguous competitions fall back cleanly without fabricating fake countries."""
        country, code = CanonicalCompetitionService.get_country_for_league_name("Some Random Unregistered Tournament 2026")
        self.assertEqual(country, "International")
        self.assertEqual(code, "INT")

        # None / empty handling
        country_none, code_none = CanonicalCompetitionService.get_country_for_league_name(None)
        self.assertEqual(country_none, "International")
        self.assertEqual(code_none, "INT")

    def test_ingestion_service_canonical_alignment(self):
        """Test that ingestion correctly preserves and aligns canonical competition identities."""
        leagues = DataIngestionService.ingest_leagues(
            self.db,
            [{"name": "Italian Serie A", "country": "Italy", "season": "2025/2026"}]
        )
        self.assertEqual(len(leagues), 1)
        self.assertEqual(leagues[0].country, "Italy")
        self.assertEqual(leagues[0].name, "Italian Serie A")

    def test_api_canonical_competition_contract(self):
        """Test that FastAPI endpoints serialize the canonical 'competition' object with country and country_code."""
        league = models.League(name="German Bundesliga", country="Germany", season="2025/2026")
        self.db.add(league)
        self.db.commit()

        home_team = models.Team(name="Bayern Munich", short_code="BAY", league_id=league.id)
        away_team = models.Team(name="Borussia Dortmund", short_code="BVB", league_id=league.id)
        self.db.add_all([home_team, away_team])
        self.db.commit()

        fixture = models.Fixture(
            league_id=league.id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2),
            status="SCHEDULED",
            venue="Allianz Arena"
        )
        self.db.add(fixture)
        self.db.commit()

        # Query fixture from endpoint
        res = self.client.get(f"/api/fixtures/{fixture.id}/details")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("league_name"), "German Bundesliga")
        self.assertEqual(data.get("country"), "Germany")

    def test_school_and_youth_competition_filter(self):
        """Test accurate detection and exclusion of school, collegiate, and youth competitions."""
        # Positive cases (should be flagged as True)
        self.assertTrue(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="Scottish Cup Qualifying",
            home_team_name="ST CADOC'S",
            away_team_name="Glasgow University"
        ))
        self.assertTrue(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="EFL Trophy, Northern Group C",
            home_team_name="Northampton Town",
            away_team_name="Brighton & Hove Albion U21"
        ))
        self.assertTrue(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="NCAA Men's Soccer",
            home_team_name="Ohio State Buckeyes",
            away_team_name="Penn State Nittany Lions"
        ))
        self.assertTrue(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="US High School Championship",
            home_team_name="Oak Prep",
            away_team_name="St. Jude High School"
        ))

        # Negative cases (professional clubs - should NOT be flagged)
        self.assertFalse(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="English Premier League",
            home_team_name="Arsenal",
            away_team_name="Chelsea"
        ))
        self.assertFalse(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="Chilean Primera",
            home_team_name="Universidad Catolica",
            away_team_name="Colo-Colo"
        ))
        self.assertFalse(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name="Peruvian Primera",
            home_team_name="Universitario de Deportes",
            away_team_name="Alianza Lima"
        ))

    def test_purge_school_and_youth_competitions(self):
        """Test database purging of school/youth fixtures."""
        league = models.League(name="Cup Qualifying", country="Scotland")
        self.db.add(league)
        self.db.flush()

        t1 = models.Team(name="St Cadocs", league_id=league.id)
        t2 = models.Team(name="Glasgow University", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.flush()

        fix = models.Fixture(
            league_id=league.id,
            home_team_id=t1.id,
            away_team_id=t2.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            status="SCHEDULED"
        )
        self.db.add(fix)
        self.db.commit()

        self.assertEqual(self.db.query(models.Fixture).count(), 1)
        purged = DataIngestionService.purge_school_and_youth_competitions(self.db)
        self.assertEqual(purged, 1)
        self.assertEqual(self.db.query(models.Fixture).count(), 0)

    def test_ncaa_resolution_and_filtering(self):
        """Test that NCAA altGameNotes resolve to USA and get filtered out."""
        ident_w = CanonicalCompetitionService.resolve_competition(alt_note="NCAAW Soccer")
        self.assertEqual(ident_w.country_name, "USA")
        self.assertEqual(ident_w.country_code, "US")
        self.assertIn("ncaaw", ident_w.competition_name.lower())

        ident_m = CanonicalCompetitionService.resolve_competition(alt_note="NCAAM Soccer")
        self.assertEqual(ident_m.country_name, "USA")
        self.assertEqual(ident_m.country_code, "US")

        # Flagged by is_school_or_youth_competition
        self.assertTrue(CanonicalCompetitionService.is_school_or_youth_competition(
            league_name=ident_w.competition_name,
            home_team_name="Alabama A&M Bulldogs",
            away_team_name="Ohio Bobcats"
        ))

    def test_venezuelan_club_signature_overrides_colombian_alt_note(self):
        """Test that Venezuelan Liga FUTVE clubs are correctly identified even if ESPN mislabels altGameNote as Colombian Primera A."""
        ident = CanonicalCompetitionService.resolve_competition(
            provider_code="all",
            alt_note="Colombian Primera A",
            home_team_name="Anzoátegui FC",
            away_team_name="Monagas SC"
        )
        self.assertEqual(ident.competition_name, "Liga FUTVE")
        self.assertEqual(ident.country_name, "Venezuela")
        self.assertEqual(ident.country_code, "VE")

    def test_purge_ncaa_team_logo_url(self):
        """Test that fixtures with NCAA logo URLs are purged even if league/team names are unfamiliar."""
        league = models.League(name="Some Soccer Showcase", country="USA")
        self.db.add(league)
        self.db.flush()

        t1 = models.Team(name="Team Alpha", logo_url="https://a.espncdn.com/i/teamlogos/ncaa/500/123.png", league_id=league.id)
        t2 = models.Team(name="Team Beta", league_id=league.id)
        self.db.add_all([t1, t2])
        self.db.flush()

        fix = models.Fixture(
            league_id=league.id,
            home_team_id=t1.id,
            away_team_id=t2.id,
            match_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            status="SCHEDULED"
        )
        self.db.add(fix)
        self.db.commit()

        self.assertEqual(self.db.query(models.Fixture).count(), 1)
        purged = DataIngestionService.purge_school_and_youth_competitions(self.db)
        self.assertEqual(purged, 1)
        self.assertEqual(self.db.query(models.Fixture).count(), 0)

