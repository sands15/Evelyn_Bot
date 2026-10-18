"""Version-pinned official facts, independent of the Java/Python product tables."""
import hashlib
import json
from pathlib import Path


ORACLE = Path(__file__).with_name("fixtures") / "menu_properties_26_3.json"
# Canonical id/name/slot/property facts from official evidence, not product tables.
CONTRACT_SHA256 = "2f400271c6796c5629456a285ffaefca9c89137fe0af6dd405c63b3711acd996"
SOURCE_SHA256 = {
    "registry": "090eb143f22d8e6dc6941b6ff727892d4d38da56f6bcdb2ee2c24fe6dd98d09f",
    "bytecode": "98ad92c8f2a1101b5259829c63fa8164d5e3d2c97fb78e48b1e75e6ed65baf14",
}


def _by_id(rows, key, label):
    if type(rows) is not list or len(rows) != 25:
        raise AssertionError(f"{label}: exactly 25 menus required")
    indexed = {}
    for row in rows:
        menu_id = row.get(key) if type(row) is dict else None
        if type(menu_id) is not int or not 0 <= menu_id < 25:
            raise AssertionError(f"{label}: invalid menu ID")
        if menu_id in indexed:
            raise AssertionError(f"{label}: duplicate menu ID {menu_id}")
        indexed[menu_id] = row
    if set(indexed) != set(range(25)):
        raise AssertionError(f"{label}: menu IDs must cover 0 through 24")
    return indexed


def load_oracle(path=ORACLE):
    oracle = json.loads(Path(path).read_text(encoding="utf-8"))
    if oracle.get("minecraft_version") != "26.3" or oracle.get("protocol") != 777:
        raise AssertionError("oracle: expected Minecraft 26.3 / protocol 777")
    provenance = oracle.get("provenance", {})
    for source, sha256 in SOURCE_SHA256.items():
        if provenance.get(source, {}).get("sha256") != sha256:
            raise AssertionError(f"oracle: missing or changed {source} provenance pin")
    rows = _by_id(oracle.get("menus"), "id", "oracle")
    for row in rows.values():
        if (type(row.get("property_count")) is not int or not 0 <= row["property_count"] <= 10
                or type(row.get("slot_count")) is not int or row["slot_count"] < 1
                or not row.get("evidence")):
            raise AssertionError(f"oracle: invalid counts/evidence for menu {row['id']}")
    facts = [{key: rows[i].get(key) for key in ("id", "name", "slot_count", "property_count")}
             for i in range(25)]
    canonical = json.dumps(facts, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != CONTRACT_SHA256:
        raise AssertionError("oracle: facts differ from pinned official contract")
    return oracle


def check_observations(observations, oracle):
    observed = _by_id(observations, "menu_type_id", "observations")
    expected = _by_id(oracle.get("menus"), "id", "oracle")
    for menu_id in range(25):
        row = expected[menu_id]
        properties = observed[menu_id].get("properties")
        count = len(properties) if type(properties) is list else None
        if count != row["property_count"]:
            raise AssertionError(f"menu {menu_id} {row['name']}: property count "
                                 f"expected {row['property_count']}, observed {count}")


def check_java_output(output, validate_inventory):
    prefix = "MENU_PROPERTY_OBSERVATIONS "
    reports = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    if len(reports) != 1:
        raise AssertionError("Java gate requires exactly one menu observation report")
    observations = json.loads(reports[0])
    check_observations(observations, load_oracle())
    for observation in observations:
        validate_inventory(observation)
