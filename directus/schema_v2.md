# Directus Schema v2

## clients
- id
- phone
- full_name_cyr
- full_name_latin
- date_of_birth
- gender
- citizenship
- created_at
- updated_at

## client_passports
- id
- client_id (→clients)
- series
- number
- issued_by
- issued_date
- expiry_date
- birth_place
- mrz_raw
- passport_hash
- is_current
- ocr_confidence
- auto_accepted
- manual_check
- created_at

## intake_cases
- id
- phone
- branch (apartment_registration/registration_only)
- resident_count
- step
- status (draft/submitted/processing/done)
- created_at
- updated_at

## intake_residents
- id
- intake_case_id (→intake_cases)
- order_index
- is_main
- ocr_data (json)
- confirmed
- phone

## apartments
- id
- address
- rooms
- capacity_residents
- monthly_reg_limit
- status (free/occupied/partial/overloaded)
- owner_id (→owners)
- created_at

## owners
- id
- full_name
- phone
- payment_day
- created_at

## deals
- id
- intake_case_id (→intake_cases)
- main_client_id (→clients)
- apartment_id (→apartments)
- manager_id
- branch
- status (draft/client_data_collected/manager_review/docs_ready/awaiting_sign/awaiting_payment/active/completed/cancelled/problem)
- date_start
- date_end
- rent_amount
- deposit
- commission
- registration_fee
- contract_url
- act_url
- video_url
- created_at
- updated_at

## deal_residents
- id
- deal_id (→deals)
- client_id (→clients)
- is_main

## occupancies
- id
- deal_id (→deals)
- client_id (→clients)
- apartment_id (→apartments)
- date_start
- date_end

## registrations
- id
- deal_id (→deals)
- client_id (→clients)
- apartment_id (→apartments)
- date_start
- date_end
- status (planned/active/expiring_soon/expired/closed)
- mfc_doc_url

## payments_client
- id
- deal_id (→deals)
- type (rent/deposit/commission/registration)
- amount
- currency (RUB)
- paid_at
- confirmed_by
- payment_method (cash/transfer)

## payments_owner
- id
- owner_id (→owners)
- apartment_id (→apartments)
- amount
- currency (RUB)
- planned_date
- paid_at
- status (planned/paid/overdue)

## files_media
- id
- entity_type
- entity_id
- file_type (passport/contract/act/video/migration_card/patent/other)
- storage_url
- metadata (json)
- uploaded_by
- created_at

## audit_logs
- id
- entity_type
- entity_id
- action
- actor_id
- payload (json)
- created_at
