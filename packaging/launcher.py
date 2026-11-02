"""Entry point of both exes: without arguments the window, with them the command line."""

import sys

from bill_reader.cli import main

sys.exit(main())
