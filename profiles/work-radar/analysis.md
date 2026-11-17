# Evaluation goal

Score how attractive this item is for enterprise DevOps, SRE, or platform engineering. The score measures potential value, not permission to deploy it.

# Weighted criteria

- Relevance to DevOps, SRE, or platform engineering: 30%.
- Integration with a typical existing infrastructure stack: 20%.
- Maturity and maintainability: 20%.
- Security posture and licensing clarity: 15%.
- Operational value and potential toil reduction: 15%.

# Scoring guidance

Return a score from 0 to 10. Apply the weights consistently. A 9-10 item has strong, concrete evidence across most criteria; 7-8 is promising enough for prompt review; 5-6 is useful but conditional; 3-4 has weak fit or material unknowns; 0-2 is noise or clearly unsuitable.

Do not turn missing information into a positive assumption. State decisive evidence and unknowns in the reason. If security, maintenance, or license evidence is absent, reduce confidence in the score and say that review is required. Use three to five specific tags.
