"""Exercise the desktop engine with real folder writes and HTTP delivery; keep the evidence."""
import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from cncbridge import engine
from cncbridge.config import normalize_config
from .mock_mes import MockMES
from .testdata import make_board, demo_settings


def run_verification(workspace=None):
    """Use a new, isolated workspace. A passed result requires actual HTTP acknowledgements."""
    ws = Path(workspace) if workspace else Path(tempfile.mkdtemp(prefix="cncbridge-proof-"))
    ws.mkdir(parents=True, exist_ok=True)
    if any(ws.iterdir()):
        raise ValueError("Choose an empty verification folder so existing files are preserved.")
    report = {"ok": False, "workspace": str(ws.resolve()),
              "started_at": datetime.now().isoformat(timespec="seconds"), "checks": []}
    mes = None

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def passed(label, detail):
        report["checks"].append({"name": label, "ok": True, "detail": detail})

    try:
        mes = MockMES(ws / "mes_received", port=0).start()
        cfg = normalize_config(dict(demo_settings(ws, mes.url), stable_seconds=0))
        source = Path(cfg["source_folder"])
        source.mkdir()
        eng = engine.Engine(cfg, ws / "data")

        def drop(name, number):
            data = make_board(number, placements=12).encode()
            target = source / name
            target.write_bytes(data)
            return target, data

        for number, fmt in enumerate(("json", "csv", "txt"), 1):
            eng.cfg = normalize_config(dict(cfg, output_format=fmt,
                                           api_body="multipart" if fmt == "txt" else "raw"))
            src, original = drop(f"board_{fmt}.csv", number)
            before = len(mes.items)
            require(eng.scan_once() == 1, f"{fmt}: source was not processed")
            row = eng.ledger.recent()[0]
            shared = ws / "shared" / row["output"]
            require(len(mes.items) == before + 1, f"{fmt}: API did not accept the file")
            received = Path(mes.items[-1]["path"]).read_bytes()
            require(shared.read_bytes() == received, f"{fmt}: destinations received different bytes")
            text = received.decode("utf-8")
            require("OP-17" in text and "WO-1187" in text, f"{fmt}: manual inputs missing")
            archive = list((ws / "backup").glob(f"*/{src.name}"))
            require(not src.exists() and len(archive) == 1 and archive[0].read_bytes() == original,
                    f"{fmt}: original was not archived intact")
            require(row["shared"] == row["api"] == "ok", f"{fmt}: ledger does not confirm both routes")
            if fmt == "json":
                payload = json.loads(text)
                values = {b["name"]: b for b in payload["selections"]}
                require(values["Board serial"]["value"] == "B240001", "Wrong single-cell extraction")
                require(len(values["Placements"]["rows"]) == 12, "Variable table range incorrect")
                require(len(values["Summary"]["rows"]) == 7, "L1:M8 range incorrect")
            passed(f"{fmt.upper()} delivery, extraction and archive",
                   f"{row['output']}: shared bytes match HTTP receiver; manual inputs and original verified")

        eng.cfg = cfg
        mes.online = False
        src, _ = drop("during_outage.csv", 10)
        before = len(mes.items)
        require(eng.scan_once() == 1, "Outage blocked source processing")
        require(engine.count_queued(ws / "data") == {"shared": 0, "api": 1},
                "Routes did not queue independently")
        require(not src.exists() and len(mes.items) == before and mes.unavailable > 0,
                "Did not observe an actual HTTP outage")
        passed("Outage buffering", "Shared delivered; API returned HTTP 503; original archived; API payload retained")
        mes.online = True
        eng = engine.Engine(cfg, ws / "data")  # retry from disk with a new engine instance
        eng.scan_once()
        require(engine.count_queued(ws / "data")["api"] == 0 and len(mes.items) == before + 1,
                "Persisted queue did not recover after restart")
        passed("Recovery after restart", "A new engine drained the persisted API queue after the MES recovered")

        original = next((ws / "backup").glob("*/board_json.csv")).read_bytes()
        (source / "duplicate.csv").write_bytes(original)
        before = len(mes.items)
        eng.scan_once()
        require((ws / "backup" / "_duplicates" / "duplicate.csv").is_file()
                and len(mes.items) == before, "Duplicate was delivered again")
        passed("Duplicate guard", "Same content archived under _duplicates without another HTTP upload")

        (source / "broken.csv").write_bytes(b"only,one,line\r\n")
        eng.scan_once()
        require((ws / "backup" / "_failed" / "broken.csv").is_file(), "Invalid CSV was not quarantined")
        passed("Bad-file handling", "Unusable log moved to _failed; engine continued")

        temporary = source / "slow.csv.part"
        data = make_board(19, placements=12).encode()
        temporary.write_bytes(data[:len(data) // 2])
        before = len(mes.items)
        eng.scan_once()
        require(temporary.exists() and len(mes.items) == before, "Incomplete .part file was consumed")
        temporary.write_bytes(data)
        slow = source / "slow.csv"
        temporary.replace(slow)
        eng.cfg = normalize_config(dict(cfg, stable_seconds=30))
        eng.scan_once()
        require(slow.exists() and len(mes.items) == before, "Fresh CSV bypassed the stability gate")
        eng.cfg = cfg
        eng.scan_once()
        require(not slow.exists() and len(mes.items) == before + 1, "Completed CSV was not delivered")
        passed("Incomplete file and stability gate", "Temporary writes ignored; fresh CSV waits; completed CSV delivered")

        mes.refuse_next = True
        refused, _ = drop("refused.csv", 20)
        stamp = datetime.now().timestamp() - 10
        os.utime(refused, (stamp, stamp))
        drop("after_refusal.csv", 21)
        eng.scan_once()
        require(engine.count_rejected(ws / "data") == 1 and engine.count_queued(ws / "data")["api"] == 0,
                "A refused file blocked later deliveries")
        require(any(item["name"].startswith("after_refusal_") for item in mes.items),
                "The file after the refusal was not delivered")
        passed("File refusal isolation", "HTTP 422 moved one payload to rejected; later payload was accepted")
        report["ok"] = True
    except Exception as exc:
        report["checks"].append({"name": "Verification stopped", "ok": False, "detail": str(exc)})
    finally:
        if mes:
            mes.close()
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        (ws / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="empty folder in which to retain the proof and payloads")
    args = parser.parse_args(argv)
    result = run_verification(args.workspace)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
