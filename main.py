import os
from dotenv import load_dotenv

load_dotenv()

MENU = """
AI Career Agent
───────────────────────────────────────
1. Search for new jobs
2. Score all unscored jobs
3. Research approved companies
4. Generate documents (resume + cover letter)
5. View top jobs  (score 70+)
6. Update application status
7. Export jobs_master.xlsx
8. Export applications.xlsx
9. Run full pipeline  (steps 1–4 automatically)
0. Exit
───────────────────────────────────────
"""


def main():
    from core.database import initialize
    initialize()

    while True:
        print(MENU)
        choice = input("Enter option: ").strip()

        if choice == "0":
            print("Goodbye.")
            break
        elif choice == "1":
            from agents.search_agent import run as run_search
            run_search()
        elif choice == "2":
            from agents.scoring_agent import run as run_scoring
            run_scoring()
        elif choice == "3":
            print("Company Research Agent — coming in Phase 3.")
        elif choice == "4":
            print("Document Generation Pipeline (LangGraph) — coming in Phase 4.")
        elif choice == "5":
            print("View top jobs — coming in Phase 3.")
        elif choice == "6":
            print("Tracker Agent — coming in Phase 5.")
        elif choice == "7":
            from core.exporter import export_jobs_xlsx
            path = export_jobs_xlsx()
            print(f"Exported: {path}")
        elif choice == "8":
            print("Export applications.xlsx — coming in Phase 5.")
        elif choice == "9":
            print("Full pipeline — coming in Phase 6.")
        else:
            print("Invalid option. Enter a number 0–9.")


if __name__ == "__main__":
    main()
