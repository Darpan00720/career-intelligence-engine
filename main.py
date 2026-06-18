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
5. View top jobs  (ranked, top 20)
6. Update application status
7. Export jobs_master.xlsx
8. View analytics & recommendations
9. Run Autonomous Career Intelligence Pipeline
0. Exit
───────────────────────────────────────
"""


def _update_tracker_cli():
    """Interactive application-status update (main.py option 6)."""
    from core import tracker

    apps = tracker.list_applications()
    if not apps:
        print("No tracked applications yet. Run the pipeline or score jobs first.")
        return

    print("\nTracked applications (top 30 by score):")
    for row in apps[:30]:
        score = row.get("total_score")
        score_s = f"{score:>3}" if score is not None else "  –"
        print(f"  [{row['job_id']:>4}] {score_s}  {row.get('status',''):<18} "
              f"{(row.get('company') or '')[:22]:<22} {(row.get('title') or '')[:40]}")

    job_id = input("\nJob ID to update (blank to cancel): ").strip()
    if not job_id:
        return
    if not job_id.isdigit():
        print("Job ID must be a number.")
        return

    print("\nStatuses:")
    for i, st in enumerate(tracker.STATUSES, start=1):
        print(f"  {i}. {st}")
    sel = input("Select status number: ").strip()
    if not sel.isdigit() or not (1 <= int(sel) <= len(tracker.STATUSES)):
        print("Invalid status selection.")
        return
    status = tracker.STATUSES[int(sel) - 1]
    notes = input("Notes (optional): ").strip() or None

    tracker.set_status(int(job_id), status, notes=notes)
    print(f"✓ Job {job_id} → {status}")


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
            from agents.research_agent import run as run_research
            print(run_research())
        elif choice == "4":
            from agents.document_agent import run as run_documents
            print(run_documents())
        elif choice == "5":
            from core.pipeline import display_top_jobs
            display_top_jobs(limit=20)
        elif choice == "6":
            _update_tracker_cli()
        elif choice == "7":
            from core.exporter import export_jobs_master_xlsx
            path = export_jobs_master_xlsx()
            print(f"Exported: {path}")
        elif choice == "8":
            from core import analytics
            from core.pipeline import display_recommendations
            m = analytics.application_metrics()
            c = analytics.conversion_metrics()
            print("\nCareer Analytics")
            print("────────────────")
            for k, v in m.items():
                print(f"  {k.replace('_', ' ').title():<22}: {v}")
            print(f"  {'Conversion Rate':<22}: {c['conversion_rate']}%")
            print(f"  {'Interview Rate':<22}: {c['interview_rate']}%")
            print(f"  {'Offer Rate':<22}: {c['offer_rate']}%")
            display_recommendations(limit=20)
        elif choice == "9":
            from core.pipeline import run_autonomous_pipeline
            run_autonomous_pipeline()
        else:
            print("Invalid option. Enter a number 0–9.")


if __name__ == "__main__":
    main()
