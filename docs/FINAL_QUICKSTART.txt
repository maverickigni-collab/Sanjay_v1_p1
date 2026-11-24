SANJAY — SET UP AND RUN

Requirements: Python 3.11+ (or Python with uv installed). OpenSSH client is needed
only for SSH synchronization. Initial dependency setup needs internet or a local
package cache; subsequent application/map/LAN use does not install anything.

ONE COMMAND, from this extracted folder:
  Windows: python start.py
  Linux / Raspberry Pi: python3 start.py

No separate pip step. The launcher verifies release hashes, builds its environment
once, installs pinned dependencies once, and opens the camp web app. Keep its
terminal open. First launch asks which provisional test camp this computer owns.
After restart its identity, profile and records are retained. Never reset/delete
camp-data to resolve an error.

If another app occupies the default ports:
  python start.py --port 8904 --report-port 8906
Use python3 on Linux. Stop only your own previous app before reusing its ports.
An occupied port or existing writer is not a reason to recreate camp identities.

For an EXISTING independent camp, keep its private data where it is:
  python start.py --root "ABSOLUTE_PATH_TO_EXISTING_CAMP_DATA" --port 8898 --report-port 8902
Do not move/copy a running identity into this clean installer. A four-writer
legacy demo cannot be silently converted to a real one-writer camp.

Camp connections shows a stable short ID and a full signed public share code.
Copy the FULL code to the other camp, preview its recorded details and compare
its FULL public key against that computer or a verified SSH channel. Accept it.
A short ID alone cannot encode arbitrary offline polygons. Codes change only
when the public camp profile changes; they cannot discover or survey boundaries.
Current four bundled profiles are provisional Vadodara TEST sites. Physical
perimeters remain unknown until recorded; responsibility areas start synthetic.

SSH pairing requires one-time known-host verification and a dedicated transport
key in camp-data/.ssh/transport_ed25519. Keep its private key here. Authorise only
its public key on the peer, preserving other keys, with an absolute forced command:
  restrict,command="python3 /ABSOLUTE/SANJAY/tools/ssh_bridge.py --port 8890" ssh-ed25519 PUBLIC_KEY
Optionally restrict the key's source IP. Configure host/user/path/ports in Camp
connections and enable automatic exchange. Both apps must run; one SSH connection
moves signed supply events BOTH ways every 15 seconds. No databases/private node
keys, phone contacts/reports or local route choices are copied. Accept a revised
profile code deliberately when a peer changes its geography. No automatic trust.

VERIFY THIS FOLDER (no dependency setup): python start.py --check
RUN EXPORTED TESTS after setup: .venv/Scripts/python.exe -m unittest discover -s tests -v
On Linux use .venv/bin/python. Node is optional for tests/location_picker_check.cjs.
Release hashes detect corruption/changed files; they are not publisher signatures.
Source tests and tools are included so this folder can be checked and rebuilt.

SECURITY / LIMITS
The operator has no login token at the user's request and is localhost-only.
Any local user/process can operate it. Host/Origin/CSRF checks remain. Phone intake
is separate and invitation-only. LAN HTTP is unencrypted; use trusted test Wi-Fi.
Private signing keys and Wi-Fi configs stay in camp-data. Never distribute that
folder or an already-run installation as a clean installer.

Four role names, 1 MB packets and 500 ledger events are prototype limits. Capacity,
conflicts and invalid signatures must fail visibly, never truncate or claim sync.
There is no production pagination, automatic key rotation, writable stale-backup
recovery, independent multiuser approval, guaranteed safe routing or boot service.
Pi hotspot provisioning is not included. Maps/camp zones are not current field
availability or official authorisation. Physical phone/WAN-off/USB/reboot tests
are separate gates, not established by the automated suite.

Source data: © OpenStreetMap contributors, ODbL 1.0;
https://www.openstreetmap.org/copyright . Building outlines cover only four camp
neighbourhoods. Optional Copernicus Sentinel-2 imagery remains native 10 m.
React Bits license: REACT_BITS_LICENSE.txt. Other notices remain in bundled assets.
