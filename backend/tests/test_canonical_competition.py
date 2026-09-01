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
