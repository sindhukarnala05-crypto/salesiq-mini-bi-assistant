# AI and Development Notes

> Edit this document so it accurately reflects the tools and problems you personally encountered. Do not claim to have used tools or encountered bugs that you did not use or experience.

## AI tools used
- ChatGPT: used for planning the application architecture, outlining the supported analytics, generating an initial implementation, and preparing test/documentation ideas.
- Other tools: [Add only if actually used, e.g. VS Code, GitHub Copilot.]

## How AI was used
AI assistance was used to draft boilerplate Flask routes, suggest a normalized SQLite schema, design a small dashboard, and propose test cases. I reviewed the generated code, ran it locally, and checked that answers were based on SQL results returned from the database.

## Problems encountered and debugging
- CSV column names can differ between datasets. The supplied company CSV contains quantity, unit price, discount and status rather than a precomputed revenue field, so revenue is calculated as quantity × unit price − discount. Returned transactions and invalid records are excluded.
- Natural-language questions can be ambiguous. I limited the application to a documented list of intents and return an error for unsupported requests.
- Missing or malformed values can corrupt aggregates. I used Pandas date/numeric coercion, removed invalid rows, and reported the number removed.
- [Add the actual runtime error you encountered and how you fixed it.]

## What I would improve with another day
- Add a validated LLM intent classifier with a strict schema and confidence threshold.
- Preview CSV data and ask the user to confirm before replacing the current database.
- Support additional questions, date ranges, currency detection, and more robust month parsing.
- Add authentication, deployment settings, logging, and stronger security controls.
- Test with more variations of real business questions and the actual company dataset.
