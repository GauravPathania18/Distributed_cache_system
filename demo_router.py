import requests


ROUTER = "http://localhost:8000"

KEYS = [
    "user:101",
    "user:102",
    "user:103",
    "user:104",
    "user:105",
    "product:101",
    "product:102",
    "product:103"
]


def main():

    print("=" * 50)
    print("DISTRIBUTED CACHE - ROUTER DISTRIBUTION DEMO")
    print("=" * 50)

    print("\nMake sure the router (:8000) and 3 nodes (:8001-:8003) are running.\n")

    # ---------------------------------------------------------
    # PUT through the router
    # ---------------------------------------------------------

    print("PUT (through router :8000):")
    print("---------------------------")

    for key in KEYS:

        response = requests.put(
            f"{ROUTER}/cache/{key}",
            json={"value": {"key": key}, "ttl": 60},
            timeout=3
        )

        if response.ok:
            node = response.json()["node"]
        else:
            node = f"ERROR {response.status_code}"

        print(f"  {key:15} -> {node}")

    # ---------------------------------------------------------
    # GET through the router
    # ---------------------------------------------------------

    print("\nGET (through router :8000):")
    print("---------------------------")

    for key in KEYS:

        response = requests.get(
            f"{ROUTER}/cache/{key}",
            timeout=3
        )

        if response.ok:
            body = response.json()
            node = body["node"]
            value = body["data"]["value"]
        else:
            node = f"ERROR {response.status_code}"
            value = None

        print(f"  {key:15} -> {node}  value={value}")


if __name__ == "__main__":
    main()