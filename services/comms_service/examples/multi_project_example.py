"""
Multi-Project Communications Example

Shows how to create drafts for different ventures (ChillGuard, PartSpec, etc.)
with proper project tagging for the Comms Dashboard filter system.

Run this after comms_service is running:
    python tools/comms_service/examples/multi_project_example.py
"""

import sys
from pathlib import Path

# Add comms_service to path
COMMS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(COMMS_DIR))

from comms_client import CommsClient


def main():
    # Initialize client
    comms = CommsClient(base_url="http://localhost:8001")

    print("[>>] Creating drafts for multiple projects...")
    print()

    # ============================================================================
    # ChillGuard Content
    # ============================================================================

    print("[*] Creating ChillGuard social post...")
    chillguard_post = comms.draft_social_post(
        agent_id="chillguard_marketing",
        platform="twitter",
        text="Freeze damage costs pool owners thousands every year. ChillGuard prevents it with real-time monitoring and instant alerts. One prevented freeze = system paid for.",
        link_url="https://chillguard.example.com",
        campaign_id="chillguard_winter_campaign",  # <-- Project tag
        metadata={
            "project": "chillguard",  # <-- Alternative way to tag
            "content_type": "product_awareness"
        }
    )
    print(f"[OK] ChillGuard post created: {chillguard_post.post_id}")
    print()

    print("[*] Creating ChillGuard email draft...")
    chillguard_email = comms.draft_email(
        agent_id="chillguard_sales",
        to=["lead@poolcompany.com"],
        subject="Prevent freeze damage before it costs you thousands",
        body_text="""Hi there,

Winter is coming, and freeze damage is one of the most expensive problems pool owners face - often $800-3000 in repairs.

ChillGuard monitors temperature AND power status 24/7, alerting you instantly when conditions turn dangerous.

Would you like to schedule a demo?

Best regards,
ChillGuard Team""",
        metadata={
            "project": "chillguard",
            "campaign_id": "chillguard_winter_outreach"
        }
    )
    print(f"[OK] ChillGuard email created: {chillguard_email.draft_id}")
    print()

    # ============================================================================
    # PartSpec Content
    # ============================================================================

    print("[*] Creating PartSpec social post...")
    partspec_post = comms.draft_social_post(
        agent_id="partspec_marketing",
        platform="linkedin",
        text="""Finding the right pool equipment part shouldn't take hours.

PartSpec AI instantly identifies parts from photos and finds the best price across suppliers.

Technicians save 2-3 hours per week on part sourcing.""",
        link_url="https://partspec.ai",
        campaign_id="partspec_launch",
        metadata={
            "project": "partspec",
            "content_type": "product_launch"
        }
    )
    print(f"[OK] PartSpec post created: {partspec_post.post_id}")
    print()

    print("[*] Creating PartSpec email draft...")
    partspec_email = comms.draft_email(
        agent_id="partspec_sales",
        to=["service@pooltechs.com"],
        subject="Stop wasting time hunting for pool parts",
        body_text="""Hi,

Your technicians spend hours every week:
- Looking up part numbers in manuals
- Calling suppliers for pricing
- Ordering wrong parts (and doing returns)

PartSpec AI solves this:
1. Take a photo of the part
2. Get instant identification + pricing
3. Order from the best supplier

Want to see it in action?

Best,
PartSpec Team""",
        metadata={
            "project": "partspec",
            "campaign_id": "partspec_b2b_outreach"
        }
    )
    print(f"[OK] PartSpec email created: {partspec_email.draft_id}")
    print()

    # ============================================================================
    # Patent Intel Content
    # ============================================================================

    print("[*] Creating Patent Intel calendar event...")
    from datetime import datetime, timedelta
    tomorrow = datetime.utcnow() + timedelta(days=1)

    patent_event = comms.draft_calendar_event(
        agent_id="patent_monitor",
        summary="Review New IoT Pool Monitoring Patents",
        start=tomorrow.replace(hour=10, minute=0),
        end=tomorrow.replace(hour=11, minute=0),
        description="""Weekly review of new patent filings in pool monitoring and IoT space.

3 new patents filed this week in:
- Temperature monitoring systems
- Smart pool equipment
- Predictive maintenance algorithms""",
        metadata={
            "project": "patent",
            "review_type": "competitive_intelligence"
        }
    )
    print(f"[OK] Patent Intel event created: {patent_event.event_id}")
    print()

    # ============================================================================
    # General Business Content
    # ============================================================================

    print("[*] Creating general business email...")
    general_email = comms.draft_email(
        agent_id="BOSS_agent",
        to=["team@company.com"],
        subject="Weekly team sync - Thursday 2pm",
        body_text="""Team,

Weekly sync scheduled for Thursday at 2pm.

Agenda:
- ChillGuard winter campaign results
- PartSpec beta launch prep
- Patent landscape review

See you there!""",
        metadata={
            "project": "general",
            "email_type": "internal"
        }
    )
    print(f"[OK] General email created: {general_email.draft_id}")
    print()

    # ============================================================================
    # Summary
    # ============================================================================

    print("[OK] Created 6 drafts across 3 projects:")
    print("     - ChillGuard: 2 items (social + email)")
    print("     - PartSpec: 2 items (social + email)")
    print("     - Patent Intel: 1 item (calendar)")
    print("     - General: 1 item (email)")
    print()
    print("[>>] Open http://localhost:5000/comms to review and approve")
    print("     Use the project filter buttons to view by venture!")


if __name__ == "__main__":
    main()
