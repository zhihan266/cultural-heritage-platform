"""Read public evidence plus an existing task's attachments; does not write public vectors."""

import argparse
import asyncio

from app.modules.rag.task_context import retrieve_task_rag_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    context = asyncio.run(retrieve_task_rag_context(args.task_id, args.query))
    print(context or "No evidence found")


if __name__ == "__main__":
    main()
