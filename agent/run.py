"""CLI: python3 -m agent.run "why did predicted churn rise among recently acquired subscribers last month?" """

import sys

from agent.orchestrator import run_investigation


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 -m agent.run "<question>"')
        sys.exit(1)
    question = sys.argv[1]
    record = run_investigation(question)
    print(f"\n=== {record['run_id']} ({record['num_tool_calls']} tool calls, log: {record['log_path']}) ===\n")
    print(record["answer"])


if __name__ == "__main__":
    main()
