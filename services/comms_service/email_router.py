"""
AI-powered email router for incoming emails.
Uses LLM Router (local-first) to classify and route emails to appropriate agents.
Falls back to Claude API when local models aren't confident enough.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
import yaml

# LLM Router (local-first routing)
try:
    _tools_dir = str(Path(__file__).resolve().parent.parent)
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
    from llm_router import LLMRouter
    _LLM_ROUTER_AVAILABLE = True
except ImportError:
    _LLM_ROUTER_AVAILABLE = False

from models import (
    ReceivedEmail,
    EmailClassification,
    EmailRoutingDecision,
    EmailIntent,
    Priority,
    ReceivedEmailStatus
)


class EmailRouter:
    """AI-powered email classification and routing."""

    def __init__(
        self,
        config_path: str = "config/email_routing_rules.yaml",
        anthropic_api_key: Optional[str] = None
    ):
        self.config_path = Path(config_path)
        self.routing_rules = self._load_routing_rules()

        # Initialize LLM Router (local-first, Claude API fallback handled by router)
        self._router = None

        if _LLM_ROUTER_AVAILABLE:
            self._router = LLMRouter()
        else:
            print("[!] LLM Router not available - email classification will use rule-based only")

    def _load_routing_rules(self) -> Dict:
        """Load routing rules from YAML config."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f)
            except Exception as e:
                print(f"[!] Failed to load routing rules: {e}, using defaults")

        # Default rules
        return {
            "rules": [
                {
                    "pattern": "schedule|meeting|calendar|appointment",
                    "agent": "calendar_agent",
                    "priority": "high",
                    "intent": "scheduling"
                },
                {
                    "pattern": "quote|pricing|purchase|buy|order",
                    "agent": "sales_agent",
                    "priority": "high",
                    "intent": "sales"
                },
                {
                    "pattern": "bug|error|crash|not working|issue|problem",
                    "agent": "tech_support_agent",
                    "priority": "high",
                    "intent": "support"
                },
                {
                    "pattern": "marketing|campaign|social media|content",
                    "agent": "marketing_agent",
                    "priority": "normal",
                    "intent": "marketing"
                },
                {
                    "pattern": "feedback|suggestion|improvement",
                    "agent": "BOSS_agent",
                    "priority": "normal",
                    "intent": "feedback"
                }
            ],
            "default_agent": "BOSS_agent",
            "spam_keywords": [
                "viagra", "casino", "lottery", "winner", "congratulations",
                "click here now", "limited time offer", "act now"
            ]
        }

    def classify_email(self, email: ReceivedEmail) -> EmailClassification:
        """
        Use Claude API to classify email intent, priority, and routing.

        Args:
            email: ReceivedEmail object

        Returns:
            EmailClassification with AI analysis
        """
        # Build classification prompt
        prompt = self._build_classification_prompt(email)

        try:
            # Route through LLM Router (local-first, Claude API fallback handled by router)
            if not self._router:
                raise RuntimeError("LLM Router not available for email classification")

            result = self._router.complete(
                prompt=prompt,
                task_type="email_classification",
                agent_name="email_agent",
                max_tokens=1024,
                temperature=0.0,
            )
            if not result.success:
                raise RuntimeError(f"LLM Router failed: {result.error}")
            result_text = result.text
            classification_data = json.loads(result_text)

            # Map to EmailClassification model
            classification = EmailClassification(
                intent=EmailIntent(classification_data.get("intent", "general")),
                priority=Priority(classification_data.get("priority", "normal")),
                suggested_agent=classification_data.get("suggested_agent", "BOSS_agent"),
                confidence=classification_data.get("confidence", 0.5),
                reasoning=classification_data.get("reasoning", ""),
                entities=classification_data.get("entities", {}),
                sentiment=classification_data.get("sentiment", "neutral"),
                requires_urgent_response=classification_data.get("requires_urgent_response", False),
                is_spam=classification_data.get("is_spam", False),
                spam_indicators=classification_data.get("spam_indicators", [])
            )

            print(f"[OK] Classified email {email.email_id}: {classification.intent.value} -> {classification.suggested_agent} (confidence: {classification.confidence:.2f})")
            return classification

        except Exception as e:
            print(f"[X] Failed to classify email {email.email_id}: {e}")

            # Fallback classification
            return EmailClassification(
                intent=EmailIntent.UNKNOWN,
                priority=Priority.NORMAL,
                suggested_agent="BOSS_agent",
                confidence=0.0,
                reasoning=f"Classification failed: {str(e)}",
                entities={},
                sentiment="neutral",
                requires_urgent_response=False,
                is_spam=False,
                spam_indicators=[]
            )

    def _build_classification_prompt(self, email: ReceivedEmail) -> str:
        """Build prompt for Claude API email classification."""

        # Get available agents from routing rules
        available_agents = list(set([
            rule.get("agent") for rule in self.routing_rules.get("rules", [])
        ]))
        available_agents.append(self.routing_rules.get("default_agent", "BOSS_agent"))

        prompt = f"""You are an email classification system for the BOSS (Business Operations Support System).

Analyze the following incoming email and classify it with the following information:

**Email Details:**
From: {email.from_address} ({email.from_name or "Unknown"})
To: {", ".join(email.to)}
Subject: {email.subject}

Body:
{email.body_text[:2000]}  # Truncate long emails

**Your task:**
Classify this email and provide a JSON response with the following structure:

```json
{{
  "intent": "<one of: support, sales, scheduling, feedback, marketing, general, spam, unknown>",
  "priority": "<one of: low, normal, high, urgent>",
  "suggested_agent": "<agent_id from list below>",
  "confidence": <0.0-1.0>,
  "reasoning": "<brief explanation of classification>",
  "entities": {{
    "product_mentions": [],
    "dates": [],
    "people": [],
    "companies": [],
    "keywords": []
  }},
  "sentiment": "<positive, negative, or neutral>",
  "requires_urgent_response": <true/false>,
  "is_spam": <true/false>,
  "spam_indicators": []
}}
```

**Available Agents:**
{", ".join(available_agents)}

**Routing Guidelines:**
- sales_agent: Inquiries about pricing, quotes, purchases, orders
- calendar_agent: Scheduling requests, meetings, appointments
- tech_support_agent: Bug reports, technical issues, support requests
- marketing_agent: Marketing campaigns, content, social media
- BOSS_agent: General inquiries, feedback, or unclear intent

**Spam Detection:**
Check for: unsolicited offers, phishing attempts, suspicious links, generic mass emails

**Priority Guidelines:**
- URGENT: Legal issues, security incidents, executive requests
- HIGH: Sales opportunities, time-sensitive requests, customer complaints
- NORMAL: General inquiries, feedback, information requests
- LOW: Newsletters, automated notifications, low-priority updates

Respond with ONLY the JSON object, no additional text.
"""
        return prompt

    def apply_rule_based_classification(self, email: ReceivedEmail) -> Optional[EmailClassification]:
        """
        Apply simple rule-based classification before AI.
        Returns classification if confident, None otherwise.

        Args:
            email: ReceivedEmail object

        Returns:
            EmailClassification if rule matches with high confidence, None otherwise
        """
        email_text = f"{email.subject} {email.body_text}".lower()

        # Check spam keywords first
        spam_keywords = self.routing_rules.get("spam_keywords", [])
        spam_found = [kw for kw in spam_keywords if kw.lower() in email_text]
        if spam_found:
            return EmailClassification(
                intent=EmailIntent.SPAM,
                priority=Priority.LOW,
                suggested_agent="BOSS_agent",
                confidence=0.9,
                reasoning=f"Spam keywords detected: {', '.join(spam_found)}",
                entities={},
                sentiment="neutral",
                requires_urgent_response=False,
                is_spam=True,
                spam_indicators=spam_found
            )

        # Check pattern rules
        for rule in self.routing_rules.get("rules", []):
            pattern = rule.get("pattern", "")
            keywords = [kw.strip() for kw in pattern.split("|")]

            if any(kw in email_text for kw in keywords):
                return EmailClassification(
                    intent=EmailIntent(rule.get("intent", "general")),
                    priority=Priority(rule.get("priority", "normal")),
                    suggested_agent=rule.get("agent", "BOSS_agent"),
                    confidence=0.7,
                    reasoning=f"Matched rule pattern: {pattern}",
                    entities={},
                    sentiment="neutral",
                    requires_urgent_response=rule.get("priority") == "urgent",
                    is_spam=False,
                    spam_indicators=[]
                )

        # No confident rule match
        return None

    def route_email(self, email: ReceivedEmail, use_ai: bool = True) -> EmailRoutingDecision:
        """
        Route email to appropriate agent.

        Args:
            email: ReceivedEmail object
            use_ai: Use AI classification (True) or rules only (False)

        Returns:
            EmailRoutingDecision with routing information
        """
        # Try rule-based first (fast)
        classification = self.apply_rule_based_classification(email)

        # Fall back to AI if no confident rule match
        if classification is None or classification.confidence < 0.7:
            if use_ai:
                print(f"[*] No confident rule match, using AI classification for {email.email_id}")
                classification = self.classify_email(email)
            else:
                # Use default agent
                classification = EmailClassification(
                    intent=EmailIntent.GENERAL,
                    priority=Priority.NORMAL,
                    suggested_agent=self.routing_rules.get("default_agent", "BOSS_agent"),
                    confidence=0.5,
                    reasoning="No rule match, using default agent",
                    entities={},
                    sentiment="neutral",
                    requires_urgent_response=False,
                    is_spam=False,
                    spam_indicators=[]
                )

        # Handle spam
        if classification.is_spam:
            email.status = ReceivedEmailStatus.SPAM
            suggested_actions = ["Mark as spam", "Block sender"]
        else:
            email.status = ReceivedEmailStatus.ROUTED
            suggested_actions = self._get_suggested_actions(classification)

        # Update email with classification
        email.intent = classification.intent
        email.priority = classification.priority
        email.routed_to_agent = classification.suggested_agent
        email.classification = classification.model_dump()
        email.entities = classification.entities
        email.sentiment = classification.sentiment

        # Create routing decision
        decision = EmailRoutingDecision(
            email_id=email.email_id,
            agent_id=classification.suggested_agent,
            priority=classification.priority,
            reasoning=classification.reasoning,
            auto_response=self._should_auto_respond(classification),
            suggested_actions=suggested_actions
        )

        print(f"[OK] Routed email {email.email_id} to {decision.agent_id} (priority: {decision.priority.value})")
        return decision

    def _get_suggested_actions(self, classification: EmailClassification) -> List[str]:
        """Generate suggested actions based on classification."""
        actions = []

        if classification.intent == EmailIntent.SALES:
            actions.append("Draft quote or pricing response")
            actions.append("Add to CRM pipeline")
        elif classification.intent == EmailIntent.SCHEDULING:
            actions.append("Create calendar event draft")
            actions.append("Check availability")
        elif classification.intent == EmailIntent.SUPPORT:
            actions.append("Create support ticket")
            actions.append("Check known issues")
        elif classification.intent == EmailIntent.MARKETING:
            actions.append("Add to marketing list")
            actions.append("Draft campaign response")

        if classification.requires_urgent_response:
            actions.insert(0, "Send immediate acknowledgment")

        if not actions:
            actions.append("Draft personalized response")

        return actions

    def _should_auto_respond(self, classification: EmailClassification) -> bool:
        """
        Determine if email should get automatic response draft.

        Args:
            classification: Email classification

        Returns:
            True if auto-response should be drafted
        """
        # Don't auto-respond to spam
        if classification.is_spam:
            return False

        # Auto-respond to high-confidence, common intents
        if classification.confidence >= 0.8 and classification.intent in [
            EmailIntent.SUPPORT,
            EmailIntent.SCHEDULING
        ]:
            return True

        return False
