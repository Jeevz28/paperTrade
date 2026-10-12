"""Restore a backup into a new experiment DB. Existing paths are never overwritten."""

import argparse
from backend.app.operations import restore_copy

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup")
    parser.add_argument("destination")
    args = parser.parse_args()
    print(restore_copy(args.backup, args.destination))
