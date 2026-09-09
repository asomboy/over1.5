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
from main import app


class TestPhase13CanonicalNormalization(unittest.TestCase):
    """
    Phase 13 Mandatory Test Suite:
    - Canonical country and competition normalization.
    - Preserves provider competition provenance.
    - Never infers country or competition from team names, team nationality, or city.
    - Cross-fixture contamination tests (same country, diff country, same competition,
      diff competition, teams with similar names across borders).
    - Unverified competitions fall back strictly to 'UNAVAILABLE' / '—'.
    """

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

    def test_canonical_competition_attributes_established(self):
        """Verify for every fixture: country, country_code, competition_name, competition_code, competition_display_name, season, round."""
        ident = CanonicalCompetitionService.resolve_competition(
            provider_code="col.1",
            league_name="Colombian Primera A",
            season_slug="2025/2026",
            provided_round="Apertura - Matchday 4"
        )
        self.assertEqual(ident.country, "Colombia")
        self.assertEqual(ident.country_code, "CO")
        self.assertEqual(ident.competition_name, "Colombian Primera A")
        self.assertEqual(ident.competition_display_name, "Primera A")
        self.assertEqual(ident.competition_code, "ESPN-col.1")
        self.assertEqual(ident.season, "2025/2026")
        self.assertEqual(ident.round_stage, "Apertura - Matchday 4")
        self.assertFalse(ident.is_international)

    def test_never_infer_country_from_team_names_or_nationality(self):
        """Teams with similar names across borders MUST NOT dictate country/competition identity."""
        # Example 1: 'Barcelona' in Ecuador LigaPro vs 'Barcelona' in Spain LaLiga
        ecu_ident = CanonicalCompetitionService.resolve_competition(
            league_name="LigaPro Ecuador",
            home_team_name="Barcelona SC",
            away_team_name="Emelec"
        )
        self.assertEqual(ecu_ident.country, "Ecuador")
        self.assertEqual(ecu_ident.country_code, "EC")
        self.assertEqual(ecu_ident.competition_display_name, "LigaPro")

        esp_ident = CanonicalCompetitionService.resolve_competition(
            league_name="Spanish LALIGA",
            home_team_name="FC Barcelona",
            away_team_name="Real Madrid"
        )
        self.assertEqual(esp_ident.country, "Spain")
        self.assertEqual(esp_ident.country_code, "ES")
        self.assertEqual(esp_ident.competition_display_name, "LaLiga")

        # Example 2: 'Arsenal' in Argentina vs 'Arsenal' in England
        arg_ident = CanonicalCompetitionService.resolve_competition(
            league_name="Argentine Liga Profesional",
            home_team_name="Arsenal de Sarandí",
            away_team_name="Banfield"
        )
        self.assertEqual(arg_ident.country, "Argentina")
        self.assertEqual(arg_ident.country_code, "AR")

        eng_ident = CanonicalCompetitionService.resolve_competition(
            league_name="English Premier League",
            home_team_name="Arsenal FC",
            away_team_name="Chelsea"
        )
        self.assertEqual(eng_ident.country, "England")
        self.assertEqual(eng_ident.country_code, "GB-ENG")

        # Example 3: 'Liverpool' in Uruguay vs 'Liverpool' in England
        uru_ident = CanonicalCompetitionService.resolve_competition(
            league_name="Uruguayan Primera Division",
            home_team_name="Liverpool Montevideo",
            away_team_name="Peñarol"
        )
        self.assertEqual(uru_ident.country, "Uruguay")
        self.assertEqual(uru_ident.country_code, "UY")

    def test_unverified_competition_never_guesses_or_fabricates(self):
        """Unverified competition returns UNAVAILABLE / '—' without guessing or inheriting."""
        ident = CanonicalCompetitionService.resolve_competition(
            league_name="Completely Unknown Mystery Tournament 2026"
        )
        self.assertEqual(ident.country, "UNAVAILABLE")
        self.assertEqual(ident.country_code, "—")
        self.assertEqual(ident.competition_name, "Completely Unknown Mystery Tournament 2026")

        # None / Empty input
        ident_none = CanonicalCompetitionService.resolve_competition()
        self.assertEqual(ident_none.country, "UNAVAILABLE")
        self.assertEqual(ident_none.country_code, "—")
        self.assertEqual(ident_none.competition_name, "UNAVAILABLE")

    def test_cross_fixture_isolation_in_database_and_api(self):
        """
        Verify multi-fixture endpoint isolation:
        - Fixture 1: England Premier League (Arsenal vs Chelsea)
        - Fixture 2: Scotland Premiership (Celtic vs Rangers)
        - Fixture 3: Colombia Primera A (Millonarios vs Santa Fe)
        - Fixture 4: Unverified Tournament (Alpha vs Beta)
        All occurring on the same date.
        Verify that each fixture retains its own verified country and competition in /api/fixtures/upcoming.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)

        l_eng = models.League(name="English Premier League", country="England", season="2025/2026")
        l_sco = models.League(name="Scottish Premiership", country="Scotland", season="2025/2026")
        l_col = models.League(name="Colombian Primera A", country="Colombia", season="2025/2026")
        l_unk = models.League(name="Unknown Showcase", country="UNAVAILABLE", season="2025/2026")
        self.db.add_all([l_eng, l_sco, l_col, l_unk])
        self.db.flush()

        t_eng1 = models.Team(name="Arsenal", league_id=l_eng.id)
        t_eng2 = models.Team(name="Chelsea", league_id=l_eng.id)
        t_sco1 = models.Team(name="Celtic", league_id=l_sco.id)
        t_sco2 = models.Team(name="Rangers", league_id=l_sco.id)
        t_col1 = models.Team(name="Millonarios", league_id=l_col.id)
        t_col2 = models.Team(name="Santa Fe", league_id=l_col.id)
        t_unk1 = models.Team(name="Alpha", league_id=l_unk.id)
        t_unk2 = models.Team(name="Beta", league_id=l_unk.id)
        self.db.add_all([t_eng1, t_eng2, t_sco1, t_sco2, t_col1, t_col2, t_unk1, t_unk2])
        self.db.flush()

        f_eng = models.Fixture(league_id=l_eng.id, home_team_id=t_eng1.id, away_team_id=t_eng2.id, match_date=now, status="SCHEDULED")
        f_sco = models.Fixture(league_id=l_sco.id, home_team_id=t_sco1.id, away_team_id=t_sco2.id, match_date=now, status="SCHEDULED")
        f_col = models.Fixture(league_id=l_col.id, home_team_id=t_col1.id, away_team_id=t_col2.id, match_date=now, status="SCHEDULED")
        f_unk = models.Fixture(league_id=l_unk.id, home_team_id=t_unk1.id, away_team_id=t_unk2.id, match_date=now, status="SCHEDULED")
        self.db.add_all([f_eng, f_sco, f_col, f_unk])
        self.db.commit()

        # Query API endpoint
        res = self.client.get("/api/fixtures/upcoming")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        fixtures = data.get("data", [])
        self.assertEqual(len(fixtures), 4)

        by_id = {f["id"]: f for f in fixtures}

        # Verify English fixture
        self.assertEqual(by_id[f_eng.id]["country"], "England")
        self.assertEqual(by_id[f_eng.id]["country_code"], "GB-ENG")
        self.assertEqual(by_id[f_eng.id]["competition_display_name"], "Premier League")

        # Verify Scottish fixture (never mislabeled as English)
        self.assertEqual(by_id[f_sco.id]["country"], "Scotland")
        self.assertEqual(by_id[f_sco.id]["country_code"], "GB-SCT")
        self.assertEqual(by_id[f_sco.id]["competition_display_name"], "Scottish Premiership")

        # Verify Colombian fixture
        self.assertEqual(by_id[f_col.id]["country"], "Colombia")
        self.assertEqual(by_id[f_col.id]["country_code"], "CO")
        self.assertEqual(by_id[f_col.id]["competition_display_name"], "Primera A")

        # Verify Unverified fixture (shows UNAVAILABLE, never guesses)
        self.assertEqual(by_id[f_unk.id]["country"], "UNAVAILABLE")
        self.assertEqual(by_id[f_unk.id]["country_code"], "—")


if __name__ == "__main__":
    unittest.main()
