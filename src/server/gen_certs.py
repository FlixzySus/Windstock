"""
``py gen_certs.py`` from the server folder -- the same tool as
``windstock/tools/gen_certs.py`` (which is the one place it is implemented).

Makes certs/ca.crt (install on every phone) + the server certificate. Uses
openssl when it is installed and pure Python otherwise. Keeps an existing CA.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from windstock.tools.gen_certs import main  # noqa: E402

if __name__ == "__main__":
    main()
