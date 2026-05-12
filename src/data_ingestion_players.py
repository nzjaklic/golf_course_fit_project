import requests
import pandas as pd
import time
from pathlib import Path

URL = "https://orchestrator.pgatour.com/graphql"

HEADERS = {
    "accept": "application/graphql-response+json, application/json",
    "accept-language": "en-US,en;q=0.6",
    "content-type": "application/json",
    "x-api-key": "da2-gsrx5bibzbb4njvhl7t37wqyl4",
    "x-pgat-platform": "web"
}

QUERY = """
query StatDetails($tourCode: TourCode!, $statId: String!, $year: Int, $eventQuery: StatDetailEventQuery) {
  statDetails(tourCode: $tourCode, statId: $statId, year: $year, eventQuery: $eventQuery) {
    statTitle
    statHeaders
    tourAvg
    rows {
      ... on StatDetailsPlayer {
        __typename
        playerId
        playerName
        country
        rank
        stats {
          statName
          statValue
        }
      }
    }
  }
}
"""

# stat IDs to collect — add more as you find them in the network tab
STAT_IDS = {
    "120":   "scoring_average",
    "02675":   "sg_total",
    "101":   "driving_distance",
    "102": "driving_accuracy_percentage",
    "103": "greens_in_regulation_percentage",
    "02568": "sg_approach",
    "02564": "sg_putting",
    "02567": "sg_off_the_tee",
    "02674": "sg_tee_to_green",
    "130": "scrambling_percentage",
    "156": "birdie_average",
    # Accuracy-related stats
    "102": "driving_accuracy_percentage",
    "02435": "rough_tendency",
    "459": "left_rough_tendency",
    "460": "right_rough_tendency",
    "080": "right_rough_tendency_rtp_score",
    "081": "left_rough_tendency_rtp_score",
    "01008": "fairway_bunker_tendency",
    "461": "missed_fairway_percent_other",
    "213": "hit_fairway_percentage",
    "02420": "distance_from_edge_of_fairway",
    "02421": "distance_from_center_of_fairway",
    "02422": "left_tendency",
    "02423": "right_tendency",
    "02438": "good_drive_percentage",
    "02331": "average_approach_distance_greater_100_yards",
    "02329": "average_approach_distance_0_to_100_yards",
}

YEARS = range(2019, 2026)


def fetch_stat(stat_id: str, year: int) -> dict:
    payload = {
        "operationName": "StatDetails",
        "query": QUERY,
        "variables": {
            "tourCode": "R",
            "statId": stat_id,
            "year": year,
            "eventQuery": None
        }
    }
    response = requests.post(URL, json=payload, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def parse_rows(data: dict, stat_id: str, stat_label: str, year: int) -> list:
    """Return normalized records: one row per player+stat entry.

    Each returned record has: year, stat_id, stat_label, stat_name, stat_value,
    player_id, player_name, country, rank.
    """
    records = []
    rows = data.get("data", {}).get("statDetails", {}).get("rows", [])
    for row in rows:
        if row.get("__typename") != "StatDetailsPlayer":
            continue
        base = {
            "year": year,
            "stat_id": stat_id,
            "stat_label": stat_label,
            "player_id": row.get("playerId"),
            "player_name": row.get("playerName"),
            "country": row.get("country"),
            "rank": row.get("rank"),
        }
        for s in row.get("stats", []):
            rec = base.copy()
            rec["stat_name"] = s.get("statName")
            rec["stat_value"] = s.get("statValue")
            records.append(rec)
    return records


def parse_primary_metric(data: dict, stat_id: str, stat_label: str, year: int) -> list:
    """Return one record per player with the primary metric for this stat.

    Primary metric is taken from `statHeaders[0]` if available, otherwise the
    first entry in `row['stats']`.
    """
    records = []
    stat_headers = data.get("data", {}).get("statDetails", {}).get("statHeaders", []) or []
    primary_name = stat_headers[0] if stat_headers else None
    rows = data.get("data", {}).get("statDetails", {}).get("rows", [])
    for row in rows:
        if row.get("__typename") != "StatDetailsPlayer":
            continue
        # Determine primary stat entry
        stats = row.get("stats", [])
        primary_value = None
        if primary_name:
            for s in stats:
                if s.get("statName") == primary_name:
                    primary_value = s.get("statValue")
                    break
        if primary_value is None and stats:
            primary_value = stats[0].get("statValue")

        record = {
            "year": year,
            "stat_id": stat_id,
            "stat_label": stat_label,
            "player_id": row.get("playerId"),
            "player_name": row.get("playerName"),
            "country": row.get("country"),
            "rank": row.get("rank"),
            "primary_metric_name": primary_name or (stats[0].get("statName") if stats else None),
            "primary_metric_value": primary_value,
        }
        records.append(record)
    return records


def get_output_path() -> Path:
    # Notebook may run from /notebooks, so detect project root safely.
    cwd = Path.cwd()
    if (cwd / "data").exists():
        project_root = cwd
    elif (cwd.parent / "data").exists():
        project_root = cwd.parent
    else:
        project_root = cwd
    output_path = project_root / "data" / "raw" / "pga_stats.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def main():
    # Build a mapping of (player_id, year) -> aggregated row with one column per stat_label
    player_year = {}

    for stat_id, stat_label in STAT_IDS.items():
        for year in YEARS:
            print(f"Fetching {stat_label} ({stat_id}) — {year}...")
            try:
                data = fetch_stat(stat_id, year)
                records = parse_primary_metric(data, stat_id, stat_label, year)
                print(f"  Got {len(records)} rows")
                for r in records:
                    key = (r["player_id"], r["year"]) 
                    base = player_year.setdefault(key, {
                        "player_id": r["player_id"],
                        "player_name": r["player_name"],
                        "country": r.get("country"),
                        "year": r["year"],
                    })
                    # set the stat column to the primary metric value
                    base[stat_label] = r.get("primary_metric_value")
            except Exception as e:
                print(f"  Failed: {e}")
            time.sleep(0.5)

    df = pd.DataFrame(list(player_year.values()))
    # ensure processed dir exists
    proc_path = get_output_path().parent.parent / "processed"
    proc_path.mkdir(parents=True, exist_ok=True)
    output_processed = proc_path / "player_year_stats.csv"
    df.to_csv(output_processed, index=False)
    print(f"\nDone. Saved {len(df)} player-year rows to {output_processed}")


if __name__ == "__main__":
    main()