# Scheme Dataset Consolidation Report

Generated: 2026-09-19

## CENTRAL
- Original scheme count: **79**
- Final scheme count: **79**
- Original rule count: **227**
- Final rule count: **227**
- Needs-review count: **4**
- Scheme duplicates removed/merged: **0**
- Duplicate rules removed: **0**

## STATE
- Original Batch 1 scheme count: **78**
- Original Batch 2 scheme count: **140**
- Combined pre-dedup count: **218**
- Final deduplicated scheme count: **218**
- Original combined rule count: **448**
- Final rule count: **448**
- Needs-review count: **1**
- Scheme duplicates removed/merged: **0**
- Duplicate rules removed: **0**

### State coverage
- Bihar: **19**
- Chhattisgarh: **20**
- Goa: **20**
- Gujarat: **20**
- Jharkhand: **20**
- Madhya Pradesh: **18**
- Maharashtra: **20**
- Odisha: **20**
- Rajasthan: **22**
- Uttar Pradesh: **19**
- West Bengal: **20**

## Schema validation
- Central master/review schema: **41 columns**
- State Batch 1 master schema: **41 columns**
- State Batch 2 master schema: **41 columns**
- Final State master schema: **41 columns**
- Final State rule schema: **7 columns**
- Central rule schema preserved at **10 columns**
- Malformed source rows: **0**
- Exact duplicate Central scheme rows after cleanup: **0**
- Exact duplicate State scheme rows after cleanup: **0**
- Exact duplicate Central rule rows after cleanup: **0**
- Exact duplicate State rule rows after cleanup: **0**

## Normalization performed
- Trimmed leading/trailing whitespace.
- Normalized exact boolean literals to lowercase `true` / `false`.
- Central master boolean cells normalized: **44**
- State master boolean cells normalized: **0**
- Safe field aliases recognized only when present: `state → state_or_ut`, `disability → disability_status`, `education → education_level`.
- Pipe-delimited multi-values were whitespace-normalized.
- No missing numeric eligibility values were inferred.

## Deduplication notes
No same-state scheme duplicates were found across the two State batches, so all **218** State records were preserved.

`Mukhyamantri Kanya Vivah Yojana` exists independently in Madhya Pradesh and Chhattisgarh. Identical-looking age/gender rules for those records were retained because the state is part of the scheme identity.

## Reference consistency
- Central orphan rules: **0**
- State orphan rules: **0**
- Central orphan needs-review rows: **0**
- State orphan needs-review rows: **0**
- Invalid confidence values: **0**
- Invalid/missing government levels: **0**
- State rows missing `state_or_ut`: **0**

## Confidence counts
### Central
- VERIFIED: **68**
- LIKELY_ACTIVE: **7**
- NEEDS_REVIEW: **4**

### State
- VERIFIED: **201**
- LIKELY_ACTIVE: **16**
- NEEDS_REVIEW: **1**

## Rare eligibility rule fields
Fields occurring at most twice across the final rule datasets:
- `already_received_rooftop_solar_subsidy`: 1
- `annual_turnover`: 1
- `area_notified`: 1
- `covered_by_EPFO_ESIC_NPS`: 1
- `crop_notified`: 1
- `family_members_already_registered`: 1
- `government_employee_in_family`: 1
- `has_bank_or_post_office_account`: 2
- `has_savings_bank_or_post_office_account`: 1
- `has_valid_electricity_connection`: 1
- `household_annual_income`: 1
- `household_has_existing_lpg_connection`: 1
- `income_restriction`: 1
- `institution_notified`: 1
- `marital_status`: 1
- `minority_status`: 2
- `nfsa_beneficiary_category`: 1
- `owns_pucca_house_anywhere_in_india`: 1
- `received_government_housing_in_last_20_years`: 1
- `regular_course`: 1
- `school_type`: 1
- `street_vendor_status`: 1
- `willing_unskilled_manual_work`: 1

## Specialized fields outside the common core
- `already_received_rooftop_solar_subsidy`: 1
- `annual_turnover`: 1
- `area_notified`: 1
- `covered_by_EPFO_ESIC_NPS`: 1
- `crop_notified`: 1
- `family_members_already_registered`: 1
- `government_employee_in_family`: 1
- `has_UDID`: 3
- `has_bank_or_post_office_account`: 2
- `has_savings_bank_or_post_office_account`: 1
- `has_valid_electricity_connection`: 1
- `household_has_existing_lpg_connection`: 1
- `income_restriction`: 1
- `institution_aicte_approved`: 3
- `institution_notified`: 1
- `is_income_tax_payer`: 3
- `nfsa_beneficiary_category`: 1
- `owns_pucca_house_anywhere_in_india`: 1
- `received_government_housing_in_last_20_years`: 1
- `receiving_other_scholarship`: 3
- `regular_course`: 1
- `school_type`: 1
- `street_vendor_status`: 1
- `willing_unskilled_manual_work`: 1

## Malformed data
- No malformed CSV rows detected.

## PostgreSQL import considerations
1. Do **not** use `scheme_name` alone as a unique key for State schemes. Use a surrogate `scheme_id` or a composite such as `(government_level, state_or_ut, scheme_name)`.
2. Preserve `state_or_ut` when resolving State rules because the same scheme name can occur in multiple states.
3. Convert blank strings to SQL `NULL` during import only if that matches your schema. The CSVs intentionally retain blanks.
4. `student_status`, `disability_status`, and `bpl_status` are not pure Boolean columns in all rows; some contain descriptive eligibility text. Store them as text unless you later split Boolean and descriptive semantics.
5. Pipe-delimited values such as `SC|ST` are preserved text and may later be normalized into junction tables.
6. Central rules have extra metadata (`value_type`, `rule_group`, `source_basis`, `confidence`) that State rules do not. If you unify them in one database table, make those columns nullable rather than discarding them.
7. The rule `value` column is textual even when it contains a number. Cast by field/operator, not globally.
8. Import as UTF-8.

## Final validation result
All requested structural checks passed: every rule references an existing scheme, every review row references an existing scheme, all NEEDS_REVIEW schemes are represented, no exact duplicate rows remain, Central/State government levels are valid, State rows have a state, confidence values are valid, and all final CSVs parse successfully.
