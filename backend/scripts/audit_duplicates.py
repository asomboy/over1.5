import sys
from datetime import datetime, timezone, timedelta
from database import SessionLocal
from services.fixture_duplicate_detection_service import FixtureDuplicateDetectionService, DuplicateClassification
from models import Fixture

db = SessionLocal()
try:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    upcoming = db.query(Fixture).filter(
        Fixture.match_date >= now - timedelta(days=2),
        Fixture.match_date <= now + timedelta(days=3)
    ).all()
    print(f"Auditing upcoming window fixtures: {len(upcoming)}")

    seen_pairs = set()
    found = 0
    for i, f1 in enumerate(upcoming):
        for f2 in upcoming[i + 1:]:
            pair_key = tuple(sorted([f1.id, f2.id]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            cmp = FixtureDuplicateDetectionService.compare_fixtures(f1, f2)
            if cmp["classification"] in [
                DuplicateClassification.DUPLICATE_CONFIRMED.value,
                DuplicateClassification.DUPLICATE_POSSIBLE.value
            ]:
                found += 1
                h1 = f1.home_team.name if f1.home_team else '?'
                a1 = f1.away_team.name if f1.away_team else '?'
                h2 = f2.home_team.name if f2.home_team else '?'
                a2 = f2.away_team.name if f2.away_team else '?'
                print(f"Candidate #{found}: Classification = {cmp['classification']} (Confidence: {cmp.get('confidence')})")
                print(f"  Fixture A (ID {f1.id}): {h1} vs {a1} | Date: {f1.match_date} | ExtID: {f1.external_id} | League: {f1.league.name if f1.league else 'None'}")
                print(f"  Fixture B (ID {f2.id}): {h2} vs {a2} | Date: {f2.match_date} | ExtID: {f2.external_id} | League: {f2.league.name if f2.league else 'None'}")
                print(f"  Reasons: {cmp['reasons']}\n")

    print(f"Total potential duplicate candidates identified: {found}")
finally:
    db.close()
