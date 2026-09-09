import sqlite3
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reconcile_leagues")

def reconcile_database(db_path: str):
    logger.info(f"Starting database league reconciliation on {db_path}...")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Ensure required canonical leagues exist
    canonical_leagues = {
        "bra_a": ("Brazilian Serie A", "Brazil", "2025/2026", "COMP-bra.1"),
        "bra_b": ("Brazilian Serie B", "Brazil", "2025/2026", "COMP-bra.2"),
        "mex_1": ("Mexican Liga BBVA MX", "Mexico", "2025/2026", "COMP-mex.1"),
        "col_1": ("Colombian Primera A", "Colombia", "2025/2026", "COMP-col.1"),
        "slv_1": ("Salvadoran Primera", "El Salvador", "2025/2026", "COMP-slv.1"),
        "conmebol_lib": ("CONMEBOL Libertadores", "South America", "2025/2026", "COMP-copa.libertadores"),
        "conmebol_sud": ("CONMEBOL Sudamericana", "South America", "2025/2026", "COMP-copa.sudamericana"),
        "por_cam": ("Portuguese Campeonato", "Portugal", "2025/2026", "COMP-por.camp")
    }

    league_ids = {}
    for key, (name, country, season, ext_id) in canonical_leagues.items():
        c.execute("SELECT id FROM leagues WHERE name = ? AND country = ?", (name, country))
        row = c.fetchone()
        if row:
            league_ids[key] = row[0]
        else:
            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).isoformat()
            c.execute("INSERT INTO leagues (name, country, season, external_id, created_at) VALUES (?, ?, ?, ?, ?)",
                      (name, country, season, ext_id, now_iso))
            league_ids[key] = c.lastrowid
            logger.info(f"Created canonical league: {name} ({country}) -> id {league_ids[key]}")

    conn.commit()

    # Define team signature dictionaries
    bra_a_teams = {
        'palmeiras', 'flamengo', 'fluminense', 'corinthians', 'cruzeiro',
        'sao paulo', 'gremio', 'internacional', 'vasco da gama',
        'atletico-mg', 'bahia', 'red bull bragantino', 'vitoria',
        'mirassol', 'remo', 'chapecoense', 'coritiba', 'athletico-pr'
    }
    bra_b_teams = {
        'botafogo-sp', 'juventude', 'fortaleza', 'goias', 'crb',
        'ceara', 'criciuma', 'avai', 'londrina', 'nautico',
        'novorizontino', 'america mineiro', 'athletic', 'vila nova',
        'cuiaba', 'sport', 'sao bernardo', 'operario pr', 'ponte preta', 'amazonas'
    }
    mex_teams = {'guadalajara', 'tigres uanl', 'fc juarez', 'pachuca', 'cruz azul', 'santos'}
    col_teams = {'cucuta deportivo', 'internacional de bogota', 'real cundinamarca', 'boyaca chico fc', 'fortaleza ceif', 'once caldas', 'jaguares de cordoba', 'deportivo pasto'}
    slv_teams = {'isidro metapan', 'c.d. platense', 'santa tecla', 'internacional santa tecla', 'cd fas', 'inca aruba', 'alianza fc'}
    conmebol_teams = {'cerro porteno', 'liga de quito', 'club olimpia', 'cienciano del cusco', 'macara'}
    por_teams = {'nazarenos', 'vitoria sernache', 'uniao montemor', 'juventude de evora'}

    def clean(s):
        import unicodedata
        if not s: return ""
        return unicodedata.normalize('NFKD', str(s)).encode('ASCII', 'ignore').decode('utf-8').strip().lower()

    # Reconcile fixtures currently under league 7 or 8 (Italy)
    c.execute("""
        SELECT f.id, ht.id, at.id, ht.name, at.name, f.league_id
        FROM fixtures f
        JOIN teams ht ON f.home_team_id = ht.id
        JOIN teams at ON f.away_team_id = at.id
        WHERE f.league_id IN (7, 8)
    """)
    rows = c.fetchall()

    repaired_fixtures = 0
    repaired_teams = set()

    for fid, hid, aid, hname, aname, current_lid in rows:
        h_clean = clean(hname)
        a_clean = clean(aname)

        target_lid = None
        if any(t in h_clean or t in a_clean for t in conmebol_teams):
            target_lid = league_ids["conmebol_lib"]
        elif any(t in h_clean or t in a_clean for t in slv_teams):
            target_lid = league_ids["slv_1"]
        elif any(t in h_clean or t in a_clean for t in col_teams):
            target_lid = league_ids["col_1"]
        elif any(t in h_clean or t in a_clean for t in mex_teams):
            target_lid = league_ids["mex_1"]
        elif any(t in h_clean or t in a_clean for t in por_teams):
            target_lid = league_ids["por_cam"]
        elif any(t in h_clean or t in a_clean for t in bra_b_teams):
            target_lid = league_ids["bra_b"]
        elif any(t in h_clean or t in a_clean for t in bra_a_teams):
            target_lid = league_ids["bra_a"]

        if target_lid and target_lid != current_lid:
            c.execute("UPDATE fixtures SET league_id = ? WHERE id = ?", (target_lid, fid))
            c.execute("UPDATE teams SET league_id = ? WHERE id = ?", (target_lid, hid))
            c.execute("UPDATE teams SET league_id = ? WHERE id = ?", (target_lid, aid))
            repaired_fixtures += 1
            repaired_teams.add(hid)
            repaired_teams.add(aid)

    # Reconcile fixtures under Argentina leagues
    c.execute("""
        SELECT f.id, ht.id, at.id, ht.name, at.name, f.league_id
        FROM fixtures f
        JOIN teams ht ON f.home_team_id = ht.id
        JOIN teams at ON f.away_team_id = at.id
        JOIN leagues l ON f.league_id = l.id
        WHERE l.country = 'Argentina'
    """)
    arg_rows = c.fetchall()
    for fid, hid, aid, hname, aname, current_lid in arg_rows:
        h_clean = clean(hname)
        a_clean = clean(aname)
        if any(t in h_clean or t in a_clean for t in slv_teams):
            target_lid = league_ids["slv_1"]
            c.execute("UPDATE fixtures SET league_id = ? WHERE id = ?", (target_lid, fid))
            c.execute("UPDATE teams SET league_id = ? WHERE id = ?", (target_lid, hid))
            c.execute("UPDATE teams SET league_id = ? WHERE id = ?", (target_lid, aid))
            repaired_fixtures += 1
            repaired_teams.add(hid)
            repaired_teams.add(aid)

    conn.commit()
    conn.close()
    logger.info(f"Reconciliation complete: {repaired_fixtures} fixtures and {len(repaired_teams)} teams reconciled.")
    return repaired_fixtures

if __name__ == "__main__":
    db_file = os.path.join(os.path.dirname(__file__), "..", "soccer.db")
    reconcile_database(db_file)
