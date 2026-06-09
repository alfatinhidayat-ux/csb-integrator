import argparse
import logging
import sys

from config import Config
from runner import SyncRunner


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Brighter API \u2192 MySQL data integrator"
    )
    parser.add_argument(
        "-e", "--env",
        action="store_true",
        help="Load config from environment variables (BRIGHTER_*)",
    )
    parser.add_argument(
        "--username", help="Brighter API username"
    )
    parser.add_argument(
        "--password", help="Brighter API password"
    )
    parser.add_argument(
        "--client-id", help="Brighter API client ID"
    )
    parser.add_argument(
        "--client-secret", help="Brighter API client secret"
    )
    parser.add_argument(
        "--cabang-ids",
        help="Comma-separated cabang IDs (default: discover from API)",
    )
    parser.add_argument(
        "--db-user", help="MySQL user"
    )
    parser.add_argument(
        "--db-password", help="MySQL password"
    )
    parser.add_argument(
        "--db-name", default="brighter_mirror", help="MySQL database name"
    )
    parser.add_argument(
        "--db-host", default="localhost", help="MySQL host"
    )
    parser.add_argument(
        "--db-port", type=int, default=3306, help="MySQL port"
    )
    parser.add_argument(
        "--results-per-page", type=int, default=100, help="API results per page"
    )
    parser.add_argument(
        "--request-delay", type=float, default=0.1, help="Delay between requests (seconds)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging"
    )
    parser.add_argument(
        "--child-only", action="store_true",
        help="Skip main sync, only run child endpoints (resume after crash)",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.env:
        config = Config.from_env()
    else:
        config = Config(
            username=args.username or input("Username: "),
            password=args.password or input("Password: "),
            client_id=args.client_id or input("Client ID: "),
            client_secret=args.client_secret or input("Client Secret: "),
            db_host=args.db_host,
            db_port=args.db_port,
            db_name=args.db_name,
            db_user=args.db_user or "root",
            db_password=args.db_password or "",
            results_per_page=args.results_per_page,
            request_delay=args.request_delay,
            cabang_ids=(
                [int(x) for x in args.cabang_ids.split(",")]
                if args.cabang_ids else None
            ),
        )

    if not config.username or not config.password:
        logging.error("Username and password are required")
        sys.exit(1)

    runner = SyncRunner(config)
    if args.child_only:
        runner.run_child_endpoints()
    else:
        runner.run_all()


if __name__ == "__main__":
    main()
