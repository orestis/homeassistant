#!/usr/bin/env python3
"""
zte-router.py — Interact with the ZTE F670L router web UI.

Commands:
  ./zte-router.py login                     Test login + list DHCP bindings
  ./zte-router.py list-dhcp                 Show existing DHCP static bindings
  ./zte-router.py show-plan                 Dry-run: show what reservations would be added
  ./zte-router.py add-reservations          Add reservations from shelly-inventory.json

Credentials default to user/user. Override with ZTE_USER / ZTE_PASS env vars.

Protocol (reverse-engineered from ZTE F670L web UI):
  - DHCP static bindings live at OBJ_DHCPBIND_ID
  - GET  /?_type=menuData&_tag=Localnet_LanMgrIpv4_DHCPStaticRule_lua.lua
    → returns XML: <OBJ_DHCPBIND_ID><Instance><ParaName>Name</><ParaValue>...
  - POST same URL with:
      IF_ACTION=Apply&Name=<name>&MACAddr=<mac>&IPAddr=<ip>&_InstID=-1&_sessionTOKEN=<tok>
    → _InstID=-1 means "create new"; response has IF_ERRORSTR=SUCC on success
  - POST with IF_ACTION=Delete&_InstID=<id>&_sessionTOKEN=<tok> to delete
"""

import hashlib
import http.cookiejar
import json
import re
import sys
import os
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64

# --- Config ---

ROUTER_URL = "http://192.168.1.1"
ROUTER_USER = os.environ.get("ZTE_USER", "user")
ROUTER_PASS = os.environ.get("ZTE_PASS", "user")
INVENTORY_FILE = Path(__file__).parent / "shelly-inventory.json"

# The URL for DHCP static binding data (GET to read, POST to write)
DHCP_STATIC_URL = "/?_type=menuData&_tag=Localnet_LanMgrIpv4_DHCPStaticRule_lua.lua"


# RSA public key extracted from the router's JS (used for integrity check header)
_RSA_PUBKEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAodPTerkUVCYmv28SOfRV
7UKHVujx/HjCUTAWy9l0L5H0JV0LfDudTdMNPEKloZsNam3YrtEnq6jqMLJV4ASb
1d6axmIgJ636wyTUS99gj4BKs6bQSTUSE8h/QkUYv4gEIt3saMS0pZpd90y6+B/9
hZxZE/RKU8e+zgRqp1/762TB7vcjtjOwXRDEL0w71Jk9i8VUQ59MR1Uj5E8X3WIc
fYSK5RWBkMhfaTRM6ozS9Bqhi40xlSOb3GBxCmliCifOJNLoO9kFoWgAIw5hkSIb
GH+4Csop9Uy8VvmmB+B3ubFLN35qIa5OG5+SDXn4L7FeAA5lRiGxRi8tsWrtew8w
nwIDAQAB
-----END PUBLIC KEY-----"""


def _integ_check(post_body):
    """Compute the integrity Check header value.

    The ZTE router requires: Check = RSA_encrypt(SHA256(postBody)).
    This mirrors the JS: selfHeader["Check"] = asyEncode(sha256(PostDataTmp))
    """
    digest = hashlib.sha256(post_body.encode()).hexdigest()
    key = RSA.import_key(_RSA_PUBKEY)
    cipher = PKCS1_v1_5.new(key)
    encrypted = cipher.encrypt(digest.encode())
    return base64.b64encode(encrypted).decode()


def _decode_js_hex(s):
    """Decode JavaScript hex escapes like \\x4e\\x44 → 'ND'.

    The router embeds session tokens as JS string literals with \\xNN escapes.
    """
    return re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)


# --- Router session ---

class ZTERouter:
    """Manage a session with the ZTE F670L router."""

    def __init__(self, base_url=ROUTER_URL):
        self.base_url = base_url
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        self.session_token = ""
        self.logged_in = False

    def _get(self, path=""):
        """HTTP GET, returns response body as string."""
        req = urllib.request.Request(f"{self.base_url}{path}")
        resp = self.opener.open(req, timeout=15)
        return resp.read().decode("utf-8", errors="replace")

    def _post(self, path, data=None, content_type="application/x-www-form-urlencoded",
              integ_check=False):
        """HTTP POST, returns response body as string.

        Args:
            integ_check: If True, compute and send the RSA integrity Check header.
                         Required for data-modifying POSTs (Apply/Delete).
        """
        data_str = data if isinstance(data, str) else (data.decode() if data else "")
        body = data_str.encode()
        headers = {
            "Content-Type": content_type,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/",
        }
        if integ_check and data_str:
            headers["Check"] = _integ_check(data_str)
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            resp = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as e:
            # Read the error response body for debugging
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            print(f"  HTTP {e.code} on POST {path}")
            if err_body:
                print(f"  Response: {err_body[:500]}")
            raise
        return resp.read().decode("utf-8", errors="replace")

    def login(self, username=ROUTER_USER, password=ROUTER_PASS):
        """Authenticate with the router.

        Flow:
          1. GET / to establish cookies + extract initial _sessionTmpToken
          2. POST /?_type=loginData&_tag=login_token to get a nonce
          3. SHA256(password + nonce)
          4. POST /?_type=loginData&_tag=login_entry with credentials
        """
        print(f"  Connecting to {self.base_url}...")

        # Step 1: Load login page (sets cookies)
        html = self._get("/")
        # Extract initial session token from the page
        tok_match = re.search(r'_sessionTmpToken\s*=\s*"([^"]*)"', html)
        if tok_match:
            self.session_token = _decode_js_hex(tok_match.group(1))
        print(f"  Cookies established.")

        # Step 2: Get login token (nonce)
        token_resp = self._post("/?_type=loginData&_tag=login_token")
        # Response is XML like <ajax_response_xml_root>TOKEN</ajax_response_xml_root>
        token_match = re.search(r">([^<]+)<", token_resp)
        if not token_match:
            print(f"  ERROR: Could not extract login token from: {token_resp[:200]}")
            sys.exit(1)
        login_token = token_match.group(1)
        print(f"  Got login nonce.")

        # Step 3: Hash password
        sha_pass = hashlib.sha256((password + login_token).encode()).hexdigest()

        # Step 4: Submit login
        post_data = urllib.parse.urlencode({
            "action": "login",
            "Username": username,
            "Password": sha_pass,
            "_sessionTOKEN": self.session_token,
        })
        login_resp = self._post("/?_type=loginData&_tag=login_entry", post_data)

        try:
            result = json.loads(login_resp)
        except json.JSONDecodeError:
            print(f"  ERROR: Unexpected login response: {login_resp[:300]}")
            sys.exit(1)

        if result.get("login_need_refresh"):
            self.session_token = result.get("sess_token", "")
            self.logged_in = True
            print(f"  ✓ Logged in as {username}")

            # After login, we need to load a page to get the real session token.
            # The router updates _sessionTmpToken on each page load.
            # Navigate to LAN IPv4 page so DHCP data endpoints become accessible.
            page_html = self._get("/?_type=menuView&_tag=lanMgrIpv4&Menu3Location=0")
            # The page contains MULTIPLE _sessionTmpToken assignments; JS
            # executes sequentially so the LAST one wins (that's the one the
            # browser would use for subsequent AJAX POSTs).
            tok_matches = re.findall(r'_sessionTmpToken\s*=\s*"([^"]*)"', page_html)
            if tok_matches:
                self.session_token = _decode_js_hex(tok_matches[-1])

            return True
        else:
            err = result.get("loginErrMsg", result)
            print(f"  ✗ Login failed: {err}")
            return False

    def logout(self):
        """Log out of the router."""
        if not self.logged_in:
            return
        try:
            self._post(
                "/?_type=loginData&_tag=logout_entry",
                urllib.parse.urlencode({
                    "IF_LogOff": 1,
                    "_sessionTOKEN": self.session_token,
                }),
            )
            print("  Logged out.")
        except Exception:
            pass
        self.logged_in = False

    def get_dhcp_bindings(self):
        """Fetch existing DHCP static bindings from the router.

        Returns list of dicts: [{"name": ..., "mac": ..., "ip": ..., "inst_id": ...}, ...]
        """
        if not self.logged_in:
            print("  ERROR: Not logged in")
            sys.exit(1)

        xml_str = self._get(DHCP_STATIC_URL)
        return _parse_dhcp_bindings_xml(xml_str)

    def add_dhcp_binding(self, name, mac, ip):
        """Add a new DHCP static binding.

        Args:
            name: Friendly name (max 10 chars on ZTE, we truncate)
            mac:  MAC address in aa:bb:cc:dd:ee:ff format
            ip:   IP address like 192.168.1.X

        Returns (success: bool, message: str)
        """
        if not self.logged_in:
            print("  ERROR: Not logged in")
            sys.exit(1)

        # Truncate name to 10 chars (router limit seen in validation rules)
        name = name[:10]

        post_body = urllib.parse.urlencode({
            "IF_ACTION": "Apply",
            "Name": name,
            "MACAddr": mac.lower(),
            "IPAddr": ip,
            "_InstID": "-1",  # -1 = new instance
            "_sessionTOKEN": self.session_token,
        })

        resp_str = self._post(DHCP_STATIC_URL, post_body, integ_check=True)

        # Response can be JSON or XML
        # On success: IF_ERRORSTR = "SUCC", may include new _InstID
        try:
            data = json.loads(resp_str)
            error_str = data.get("IF_ERRORSTR", "")
            if error_str == "SUCC":
                # Update session token if provided
                new_inst = data.get("_InstID", "")
                return True, f"OK (InstID={new_inst})"
            else:
                return False, f"{error_str} / {data.get('IF_ERRORPARAM', '')}"
        except json.JSONDecodeError:
            # Try XML parsing
            try:
                error_match = re.search(r"<IF_ERRORSTR>([^<]*)</IF_ERRORSTR>", resp_str)
                if error_match and error_match.group(1) == "SUCC":
                    return True, "OK"
                elif error_match:
                    return False, error_match.group(1)
            except Exception:
                pass
            return False, f"Unexpected response: {resp_str[:200]}"

    def delete_dhcp_binding(self, inst_id):
        """Delete a DHCP static binding by its instance ID."""
        if not self.logged_in:
            print("  ERROR: Not logged in")
            sys.exit(1)

        post_body = urllib.parse.urlencode({
            "IF_ACTION": "Delete",
            "_InstID": inst_id,
            "_sessionTOKEN": self.session_token,
        })

        resp_str = self._post(DHCP_STATIC_URL, post_body, integ_check=True)
        try:
            data = json.loads(resp_str)
            return data.get("IF_ERRORSTR") == "SUCC"
        except json.JSONDecodeError:
            return "SUCC" in resp_str


# --- XML parsing ---

def _parse_dhcp_bindings_xml(xml_str):
    """Parse the OBJ_DHCPBIND_ID XML response into a list of bindings.

    The router returns XML like:
      <ajax_response_xml_root>
        <IF_ERRORSTR>SUCC</IF_ERRORSTR>
        <OBJ_DHCPBIND_ID>
          <Instance>
            <ParaName>_InstID</ParaName><ParaValue>DEV.V4DHCP.Server.Pool1.Bind1</ParaValue>
            <ParaName>Name</ParaName><ParaValue>EAP653-UR</ParaValue>
            <ParaName>MACAddr</ParaName><ParaValue>dc:62:79:e3:45:98</ParaValue>
            <ParaName>IPAddr</ParaName><ParaValue>192.168.1.45</ParaValue>
          </Instance>
          ...
        </OBJ_DHCPBIND_ID>
      </ajax_response_xml_root>
    """
    bindings = []

    # The XML may not be well-formed, so use regex as fallback
    try:
        root = ET.fromstring(xml_str)
        obj = root.find(".//OBJ_DHCPBIND_ID")
        if obj is None:
            return bindings
        for inst in obj.findall("Instance"):
            entry = {}
            names = inst.findall("ParaName")
            values = inst.findall("ParaValue")
            for n, v in zip(names, values):
                key = n.text or ""
                val = v.text or ""
                if key == "_InstID":
                    entry["inst_id"] = val
                elif key == "Name":
                    entry["name"] = val
                elif key == "MACAddr":
                    entry["mac"] = val.upper()
                elif key == "IPAddr":
                    entry["ip"] = val
            if entry.get("mac"):
                bindings.append(entry)
        return bindings
    except ET.ParseError:
        pass

    # Fallback: regex
    instances = re.findall(r"<Instance>(.*?)</Instance>", xml_str, re.S)
    for inst_xml in instances:
        pairs = re.findall(r"<ParaName>([^<]*)</ParaName>\s*<ParaValue>([^<]*)</ParaValue>", inst_xml)
        entry = {}
        for name, value in pairs:
            if name == "_InstID":
                entry["inst_id"] = value
            elif name == "Name":
                entry["name"] = value
            elif name == "MACAddr":
                entry["mac"] = value.upper()
            elif name == "IPAddr":
                entry["ip"] = value
        if entry.get("mac"):
            bindings.append(entry)

    return bindings


# --- Load inventory ---

def load_inventory():
    """Load the Shelly inventory file."""
    if not INVENTORY_FILE.exists():
        print(f"  ERROR: {INVENTORY_FILE.name} not found. Run discover-shellys.py first.")
        sys.exit(1)
    with open(INVENTORY_FILE) as f:
        return json.load(f)


# --- Commands ---

def cmd_login(args):
    """Test login and list DHCP bindings."""
    router = ZTERouter()
    if router.login():
        bindings = router.get_dhcp_bindings()
        print(f"\n  Found {len(bindings)} existing DHCP static binding(s):")
        for b in bindings:
            print(f"    {b.get('name', '?'):<15} {b.get('mac', '?'):<20} → {b.get('ip', '?'):<18} (InstID: {b.get('inst_id', '?')})")
        router.logout()
    else:
        print("  Login failed. Check credentials (ZTE_USER/ZTE_PASS env vars).")


def cmd_list_dhcp(args):
    """Show existing DHCP static bindings, cross-referenced with inventory."""
    router = ZTERouter()
    if not router.login():
        return

    bindings = router.get_dhcp_bindings()
    inventory = load_inventory()
    inv_by_mac = {d["mac"].upper(): d for d in inventory}

    print(f"\n  {'Name':<15} {'MAC':<20} {'IP':<18} {'Shelly'}")
    print(f"  {'─' * 70}")
    for b in bindings:
        mac = b.get("mac", "").upper()
        known = inv_by_mac.get(mac)
        shelly_name = known["friendly_name"] if known else "—"
        print(f"  {b.get('name', '?'):<15} {mac:<20} {b.get('ip', '?'):<18} {shelly_name}")
    print(f"\n  Total: {len(bindings)} binding(s)")
    router.logout()


def cmd_show_plan(args):
    """Dry-run: show what DHCP bindings would be added / already exist."""
    inventory = load_inventory()

    # Try to check existing bindings too
    router = ZTERouter()
    existing_macs = set()
    if router.login():
        bindings = router.get_dhcp_bindings()
        existing_macs = {b["mac"].upper() for b in bindings if b.get("mac")}
        router.logout()

    to_add = []
    already = []
    for d in inventory:
        if d["mac"].upper() in existing_macs:
            already.append(d)
        else:
            to_add.append(d)

    if already:
        print(f"\n  Already have DHCP binding ({len(already)}):")
        for d in already:
            print(f"    ✓ {d['friendly_name']:<30} {d['mac']:<20} → {d['ip']}")

    if to_add:
        print(f"\n  Will add ({len(to_add)}):")
        for d in to_add:
            # Name is truncated to 10 chars
            name = d["friendly_name"][:10]
            print(f"    + {name:<10} {d['friendly_name']:<30} {d['mac']:<20} → {d['ip']}")
    else:
        print(f"\n  ✓ All {len(inventory)} devices already have DHCP bindings.")

    print(f"\n  Total: {len(to_add)} to add, {len(already)} already bound")
    if to_add:
        print(f"  Run 'add-reservations' to apply.")


def cmd_add_reservations(args):
    """Add DHCP static bindings from shelly-inventory.json to the router."""
    inventory = load_inventory()
    router = ZTERouter()
    if not router.login():
        return

    # Get existing bindings
    bindings = router.get_dhcp_bindings()
    existing_macs = {b["mac"].upper() for b in bindings if b.get("mac")}

    to_add = [d for d in inventory if d["mac"].upper() not in existing_macs]
    already = [d for d in inventory if d["mac"].upper() in existing_macs]

    if already:
        print(f"\n  Already bound ({len(already)}):")
        for d in already:
            print(f"    ✓ {d['friendly_name']} ({d['mac']} → {d['ip']})")

    if not to_add:
        print(f"\n  ✓ All {len(inventory)} devices already have bindings.")
        router.logout()
        return

    print(f"\n  Adding {len(to_add)} new binding(s)...\n")

    success_count = 0
    fail_count = 0
    for i, d in enumerate(to_add, 1):
        name = d["friendly_name"][:10]
        mac = d["mac"].lower()
        ip = d["ip"]

        print(f"  [{i}/{len(to_add)}] {name:<10}  {mac} → {ip} ... ", end="", flush=True)

        ok, msg = router.add_dhcp_binding(name, mac, ip)
        if ok:
            print(f"✓ {msg}")
            success_count += 1
        else:
            print(f"✗ {msg}")
            fail_count += 1

        # Small delay between requests to avoid overwhelming the router
        if i < len(to_add):
            time.sleep(1)

    print(f"\n  Done: {success_count} added, {fail_count} failed, {len(already)} already existed.")
    router.logout()


def cmd_help(args=None):
    print("zte-router.py — ZTE F670L router DHCP management")
    print()
    print("Commands:")
    print("  login              Test login and show DHCP bindings")
    print("  list-dhcp          Show existing DHCP bindings (with inventory cross-ref)")
    print("  show-plan          Dry-run: show what bindings would be added")
    print("  add-reservations   Add bindings from shelly-inventory.json")
    print()
    print("Environment variables:")
    print("  ZTE_USER   Router username (default: user)")
    print("  ZTE_PASS   Router password (default: user)")


def main():
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "login":            cmd_login,
        "list-dhcp":        cmd_list_dhcp,
        "show-plan":        cmd_show_plan,
        "add-reservations": cmd_add_reservations,
        "help":             cmd_help,
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)

    commands[cmd](args)


if __name__ == "__main__":
    main()
