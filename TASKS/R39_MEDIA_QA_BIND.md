# Growth R39 — bind accepted Media R27 QA

Writable branch: agent/growth-r39-media-qa-bind-20261007
Exact start: 608ec634d9f38ecf054ccd63ba6e7f3a1cd79cae

Accepted Media QA certificate source:
foto6/boss@06ade2b1e8d40c4ab6559f3ffe1d69b0ee36fd78
hardwave_qa/certificates/growth.media_r27_independent_qa.r38.v1.json

Goal: consume the exact independent QA tuple while preserving R38 sealed-bundle verification and evidence-class separation.

Required:
- validate exact Media SHA/CI/artifact/digest/contracts;
- reject fixture-only/stale/mismatched QA;
- refresh Creator authority to the current exact successor when available;
- state must distinguish source-ready-for-real-bundle from actual real-bundle verification;
- no synthetic positive review; fixture review cannot yield ready;
- exact-head CI + deterministic artifact;
- no provider/browser/publish effects.

Return exact SHA/CI/artifact and verification command.