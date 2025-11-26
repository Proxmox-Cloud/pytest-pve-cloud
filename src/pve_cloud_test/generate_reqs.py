import yaml

def main():

    with open("galaxy.yml") as f:
        galaxy = yaml.safe_load(f)

    deps = galaxy.get("dependencies", {})

    req = {"collections": []}

    for name, version in deps.items():
        entry = {}
        if name.startswith("git+"):
            entry["name"] = name.replace("git+", "")
            entry["type"] = "git"
            entry["version"] = version
        else:
            entry["name"] = name
            entry["version"] = version

        req["collections"].append(entry)

    with open("tdd-requirements.yml", "w") as f:
        yaml.safe_dump(req, f, sort_keys=False)

    print("Generated tdd-requirements.yml")
