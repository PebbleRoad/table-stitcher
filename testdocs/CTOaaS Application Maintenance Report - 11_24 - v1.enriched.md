<!-- image -->

## Maintenance Report

| Reporting period:   | 1 November 2024 to 30 November 2024   |
|---------------------|---------------------------------------|
| Project name:       | CTOaaSApplication Maintenance         |
| Project stage:      | Maintenance                           |
| Prepared by:        | MaximeBaracco                         |
| Reviewed by:        | Vasu Kolla                            |
| Date of Submission: | 5 December 2024                       |

## 1. Accomplishments for this period

## 1. Applications versions

| Date   | Application   |   Version | Environment   | Content                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
|--------|---------------|-----------|---------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 15/11  | CTOaaS        |     0.17  | UAT           | 0.169 to 0.170 https://jira.ship.gov.sg/browse/CTOAAQZASF-1030 https://jira.ship.gov.sg/browse/CTOAAQZASF-1033 https://jira.ship.gov.sg/browse/CTOAAQZASF-1039 https://jira.ship.gov.sg/browse/CTOAAQZASF-944 https://jira.ship.gov.sg/browse/CTOAAQZASF-1022 https://jira.ship.gov.sg/browse/CTOAAQZASF-1015 https://jira.ship.gov.sg/browse/CTOAAQZASF-1016 https://jira.ship.gov.sg/browse/CTOAAQZASF-1009 https://jira.ship.gov.sg/browse/CTOAAQZASF-854 https://jira.ship.gov.sg/browse/CTOAAQZASF-1017 https://jira.ship.gov.sg/browse/CTOAAQZASF-1027 https://jira.ship.gov.sg/browse/CTOAAQZASF-1040                     |
| 18/11  | CTOaaS        |     0.171 | UAT           | 0.170 to 0.171 https://jira.ship.gov.sg/browse/CTOAAQZASF-1033                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 19/11  | CTOaaS        |     0.172 | UAT           | 0.171 to 0.172 https://jira.ship.gov.sg/browse/CTOAAQZASF-1033                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 21/11  | CTOaaS        |     0.173 | UAT           | 0.172 to 0.173 https://jira.ship.gov.sg/browse/CTOAAQZASF-1030 https://jira.ship.gov.sg/browse/CTOAAQZASF-1033                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 21/11  | CTOaaS        |     0.173 | PROD          | 0.169 (deployed on 03/10) to 0.173 https://jira.ship.gov.sg/browse/CTOAAQZASF-1030 https://jira.ship.gov.sg/browse/CTOAAQZASF-1033 https://jira.ship.gov.sg/browse/CTOAAQZASF-1039 https://jira.ship.gov.sg/browse/CTOAAQZASF-944 https://jira.ship.gov.sg/browse/CTOAAQZASF-1022 https://jira.ship.gov.sg/browse/CTOAAQZASF-1015 https://jira.ship.gov.sg/browse/CTOAAQZASF-1016 https://jira.ship.gov.sg/browse/CTOAAQZASF-1009 https://jira.ship.gov.sg/browse/CTOAAQZASF-854 https://jira.ship.gov.sg/browse/CTOAAQZASF-1017 https://jira.ship.gov.sg/browse/CTOAAQZASF-1027 https://jira.ship.gov.sg/browse/CTOAAQZASF-1040 |

## 2. Application configuration changes

| Date   | Description                                                        | Environment   | Reference/Comment                             |
|--------|--------------------------------------------------------------------|---------------|-----------------------------------------------|
| 19/11  | Change value of site property CTOaaS_Emails -> GenAI_EmailTemplate | UAT           | Email subject 'Site Property for UATandProd'  |
| 21/11  | Change value of site property CTOaaS_Emails -> GenAI_EmailTemplate | PROD          | Email subject: 'Site Property for UATandProd' |
| 26/11  | Change value of site property CTOaaS_Emails -> GenAI_EmailTemplate | All           | Email subject: 'Site Property Change Request' |

## 3. Issues on PROD

None

## 4. Other

- a. Fernando was assigned and collected a GSIB on 05/11. We will analyse an issue he faced using it during the last maintenance window (email subject: 'Fernando's GSIB unable to view prod env during maintenance window').
- b. Access to the DEV and STG environments was granted and confirmed to be working on 11/11 for 4 Simsys users (Thariq, Chinnam, Wen Jing, Khaing Su). Access is now restricted (there is no visibility on the OutSystems environment) and will be reactivated if requested by the CTOaaS development team.

## 2. Project performance

## Problem resolution analysis

| Severity Level   | Numberofrequests   | SLAMet   | SLAMet   |
|------------------|--------------------|----------|----------|
| Severity Level   | Numberofrequests   | Yes      |          |
| Very             | 0                  | 0        | Severe 0 |
| Severe           | 0                  | 0        | 0        |
| High             | 0                  | 0        | 0        |
| Medium           | 0                  | 0        | 0        |
| Low              | 0                  | 0        | 0        |
| Total            | 0                  | 0        | 0        |

## System availability

|   S/No. | Period        |   Scheduled Operation Time (SOT) | System Downtime (SD)   | System Availability*   | Remarks   |
|---------|---------------|----------------------------------|------------------------|------------------------|-----------|
|      33 | November 2024 |                              720 | 0%                     | 100%                   |           |

The System Availability level shall not be less than NINETY-NINE POINT FIVE percent (99.5%) for each calendar month ('Monthly System Availability Level').

SOT: Scheduled Operation Time is defined to be the scheduled operating hours for the System.

- SD: System Downtime is the accumulated time during which the System or its component is inoperable or partially inoperable due to system failure