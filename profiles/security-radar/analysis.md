# Evaluation goal

Score how urgently this item deserves security review for infrastructure and software delivery. Give particular weight to fresh, actionable CVEs and confirmed exploitation. The score measures review priority, not proof that the environment is vulnerable.

# Weighted criteria

- Urgency and freshness, including confirmed active exploitation or a newly disclosed vulnerability: 25%.
- Applicability to the target stack and plausible exposure: 25%.
- Quality of evidence and technical specificity: 20%.
- Availability of a concrete fix, mitigation, detection, or safe verification path: 20%.
- Broader preventive value for AppSec, DevSecOps, cloud-native security, or the software supply chain: 10%.

# CVE guidance

Raise the score when the source identifies affected products and versions, attack prerequisites or exposure conditions, credible exploitation evidence, and a vendor fix or mitigation. A current CVE known to be exploited and applicable to the target stack should normally outrank general security news.

Do not infer that a CVE is exploitable, remotely reachable, or present in the environment. Do not invent CVSS values, affected versions, exploit availability, business impact, patch status, or deployment state. CVSS alone is not evidence of actual exposure. Penalize stale, duplicated, speculative, or version-ambiguous reports.

# Scoring guidance

Return a score from 0 to 10. Apply the weights consistently. A 9-10 item needs immediate review backed by strong and applicable evidence; 7-8 deserves prompt triage; 5-6 is relevant but conditional or insufficiently scoped; 3-4 is weak, stale, or mostly informational; 0-2 is noise or unsupported.

State the decisive evidence and unknowns in the reason. Distinguish confirmed facts from inference and use three to five specific tags, including a CVE identifier when the source provides one.
