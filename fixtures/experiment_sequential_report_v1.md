# Sequential Experiment Evaluation Report v1

- Experiment seed: 720260927
- Plan SHA-256: 6e3b42c01107bbaeab34a59dc55b2fc556714b768232a2aa46ca9ec2b016e517
- Plan digest: ca76de53164fa40ac9dccc26e3d86a17ca47d9a789a8584de3bb672c9a268558
- Corpus SHA-256: cf77b5ab80e12e6d67553b2d62b87f26ab24b995fdf3b92731fbb918b08724b1
- Report SHA-256: d8b0206b1d9bfd716a0ec6cf39fec91019386e683159b4fe2c3db9daea0d2d11
- Method: bonferroni_fixed_max_looks_two_sided_z_v1
- Family-wise alpha: 0.05
- Predeclared looks: 400 / 800 / 1200 / 1600
- Per-look comparison alpha: 0.0125

- guardrail_breach: state=stop_for_guardrail, available=800, final_look=1, n=400, effect=-0.01377534, CI=[-0.09562885, 0.06807816], SRM=False, coverage=1.0, guardrails=['distribution_shift', 'recommendation_churn']
- late_replay: state=analysis_complete, available=1600, final_look=4, n=1600, effect=0.01528748, CI=[-0.02110829, 0.05168324], SRM=False, coverage=1.0, guardrails=[]
- null_effect: state=analysis_complete, available=1600, final_look=4, n=1600, effect=0.01528748, CI=[-0.02110829, 0.05168324], SRM=False, coverage=1.0, guardrails=[]
- positive_synthetic_effect: state=analysis_complete, available=1600, final_look=1, n=400, effect=0.08115203, CI=[0.00199273, 0.16031133], SRM=False, coverage=1.0, guardrails=[]
- sample_ratio_mismatch: state=stop_for_guardrail, available=800, final_look=1, n=400, effect=0.04510443, CI=[-0.05287832, 0.14308718], SRM=True, coverage=0.5, guardrails=['sample_ratio_mismatch']
- sparse_data: state=insufficient_evidence, available=120, final_look=1, n=120, effect=-0.03496503, CI=[-0.16485834, 0.09492827], SRM=False, coverage=0.0, guardrails=[]

- CreatorFeedback contract_version: 1.0 (unchanged)
- feedback_batch: growth.feedback_batch.v1 (unchanged)
- creator_seed: growth.creator_seed.v1 (unchanged)
- Auto-publish: false
- External mutation: false

Only synthetic deterministic randomized-assignment fixtures are treated as experiment evidence. Historical analytics remain observational.
