import sqlite3
import unicodedata
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reconcile_nat_league")

def clean(s):
    if not s: return ""
    return unicodedata.normalize('NFKD', str(s)).encode('ASCII', 'ignore').decode('utf-8').strip().lower()

def run():
    conn = sqlite3.connect("soccer.db")
    c = conn.cursor()

    # Targets:
    # 90: NWSL (USA)
    # 92: Greek Super League (Greece)
    # 95: Argentine Primera B (Argentina)
    # 110: USL Championship (USA)
    # 111: USL League One (USA)
    # 117: Première Ligue (France)
    # 119: Northern Super League (Canada)

    nwsl_teams = {
        'seattle reign', 'houston dash', 'boston legacy', 'angel city', 'utah royals',
        'kansas city current', 'racing louisville', 'bay fc', 'denver summit',
        'gotham fc', 'chicago stars', 'north carolina courage', 'portland thorns',
        'washington spirit', 'san diego wave', 'orlando pride'
    }

    nsl_canada_teams = {
        'afc toronto', 'ottawa rapid', 'montreal roses', 'calgary wild', 'halifax tides', 'vancouver rise'
    }

    premiere_ligue_fr = {
        'ol lyonnes', 'paris fc', 'fc fleury 91', 'saint-malo', 'le havre ac',
        'lens', 'paris saint-germain', 'marseille', 'montpellier', 'toulouse', 'strasbourg', 'nantes'
    }

    super_league_greece = {
        'levadiakos', 'panathinaikos', 'atromitos', 'kalamata', 'ofi crete', 'kifisia',
        'panetolikos', 'paok', 'asteras tripoli', 'iraklis', 'volos nfc', 'aek athens', 'aris'
    }

    arg_prim_b = {
        'comunicaciones', 'sportivo italiano', 'brown de adrogue', 'uai urquiza',
        'deportivo laferrere', 'villa dalmine', 'dock sud', 'camioneros',
        'gimnasia y tiro', 'chacarita juniors', 'los andes', 'acassuso',
        'gimnasia y esgrima (jujuy)', 'agropecuario', 'deportivo merlo',
        'colegiales', 'midland', 'ferro carril oeste', 'mitre', 'quilmes',
        'nueva chicago', 'excursionistas', 'ituzaingo'
    }

    usl_c = {
        'loudoun united', 'new mexico united', 'charleston battery', 'hartford athletic',
        'colorado springs switchbacks', 'detroit city', 'indy eleven', 'birmingham legion',
        'miami fc', 'pittsburgh riverhounds', 'sporting jax', 'lexington', 'rhode island',
        'louisville city', 'tampa bay rowdies', 'brooklyn fc', 'san antonio', 'fc tulsa',
        'monterey bay', 'phoenix rising', 'orange county', 'sacramento republic',
        'las vegas lights', 'el paso locomotive', 'oakland roots'
    }

    usl_one = {
        'westchester', 'chattanooga red wolves', 'fort wayne', 'richmond kickers',
        'new york cosmos', 'sarasota paradise', 'forward madison', 'one knoxville',
        'greenville triumph', 'fc naples', 'athletic club boise', 'spokane velocity',
        'union omaha', 'charlotte independence', 'corpus christi', 'av alta'
    }

    c.execute("""
        SELECT f.id, ht.id, at.id, ht.name, at.name
        FROM fixtures f
        JOIN teams ht ON f.home_team_id = ht.id
        JOIN teams at ON f.away_team_id = at.id
        WHERE f.league_id = 142
    """)
    rows = c.fetchall()
    reconciled = 0

    for fid, hid, aid, hname, aname in rows:
        hc = clean(hname)
        ac = clean(aname)
        target = None

        if any(t in hc or t in ac for t in nwsl_teams):
            target = 90
        elif any(t in hc or t in ac for t in nsl_canada_teams):
            target = 119
        elif any(t in hc or t in ac for t in premiere_ligue_fr):
            target = 117
        elif any(t in hc or t in ac for t in super_league_greece):
            target = 92
        elif any(t in hc or t in ac for t in arg_prim_b):
            target = 95
        elif any(t in hc or t in ac for t in usl_c):
            target = 110
        elif any(t in hc or t in ac for t in usl_one):
            target = 111

        if target:
            c.execute("UPDATE fixtures SET league_id = ? WHERE id = ?", (target, fid))
            c.execute("UPDATE teams SET league_id = ? WHERE id = ?", (target, hid))
            c.execute("UPDATE teams SET league_id = ? WHERE id = ?", (target, aid))
            reconciled += 1
            logger.info(f"Fixture {fid} ({hname} vs {aname}) -> Reconciled to league_id {target}")

    conn.commit()
    conn.close()
    logger.info(f"Reconciled {reconciled} fixtures out of {len(rows)} from league 142.")

if __name__ == "__main__":
    run()
