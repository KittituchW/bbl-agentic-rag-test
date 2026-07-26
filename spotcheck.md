# Sample query transcript

- Generated: 2026-07-26 15:22
- Provider / model: `bbl` / `gpt-5-mini`
- Semantic retrieval: on
- Knowledge base: 14 chunks
- Queries: 3 (0 errored)

## 1. Exact keyword match

**Q:** When should I upload a receipt for a meal paid with my corporate card?

```
retrieval -> 3 snippet(s)
  chunk  8 | bm25= 9.54 cosine=0.611 | via bm25+semantic | Corporate Card Policy
  chunk  2 | bm25= 1.80 cosine=0.407 | via bm25+semantic | Expense Reimbursement Policy
  chunk  0 | bm25= 3.29 cosine=0.248 | via bm25 | International Travel Policy
```

Upload the receipt to the finance portal within 7 calendar days of the meal transaction.

- Cardholders must upload receipts to the finance portal within 7 calendar days of each transaction. (Corporate Card Policy)  
- If you are also submitting an expense claim, do so through the finance portal within 30 days of the expense date and include the original receipt or electronic tax invoice. (Expense Reimbursement Policy)

*74.1s*

## 2. Incident + privacy cross-policy

**Q:** I accidentally sent a customer's account number through my personal messaging app. What rules did I violate, and what must I do now?

```
retrieval -> 3 snippet(s)
  chunk  9 | bm25=15.27 cosine=0.390 | via bm25+semantic | Customer Data Privacy Policy
  chunk  5 | bm25= 3.33 cosine=0.346 | via bm25+semantic | Information Security Policy
  chunk 10 | bm25= 1.46 cosine=0.346 | via bm25+semantic | Incident Response Policy
```

You violated the Customer Data Privacy Policy and the Information Security Policy, and you must report the accidental disclosure to IT Security immediately (within 2 hours) and preserve evidence. 

- Sending a customer’s account number through a personal messaging app is prohibited: customer personal data may be accessed only for an assigned business purpose, files containing account numbers must be encrypted before internal sharing, and customer data must not be sent to personal email accounts or consumer messaging applications. (Customer Data Privacy Policy)
- Company data may not be stored on personal devices or personal cloud storage services, so transferring the account number to a personal messaging app breaches the Information Security Policy. (Information Security Policy)
- Required immediate actions: report the accidental data disclosure to the IT Security team within 2 hours of discovery; preserve evidence; disconnect affected devices from the network if it is safe to do so; and avoid deleting suspicious files. If the incident is critical, Legal, Compliance, and the Chief Information Security Officer must be notified. (Incident Response Policy)

*155.7s*

## 3. Out-of-domain no-answer

**Q:** Who won the World Cup in 2022?

```
retrieval -> NO_RELEVANT_INFORMATION
```

The knowledge base does not contain information on who won the World Cup in 2022.

*72.0s*
