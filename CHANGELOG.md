## [1.0.3](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/compare/v1.0.2...v1.0.3) (2026-03-26)

### Bug Fixes

* update ADR-001 refs after docs/adr move ([d2e94b4](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/d2e94b4b673fa840904a999c5edfdc8ea47d7ef5))

## [1.0.2](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/compare/v1.0.1...v1.0.2) (2026-03-26)

### Performance

* run pre-push lefthook commands in parallel ([e638c0b](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/e638c0bfc8a8ce28f94b610b2d0dd48ddbd4b48d))

## [1.0.1](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/compare/v1.0.0...v1.0.1) (2026-03-26)

### Bug Fixes

* extract release notes correctly from CHANGELOG.md ([4baa362](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/4baa36253fd99ccefee115ef01f27a8133e4c203))
* remove invalid name property from lefthook commands and add --yes to npx ([dfb8c91](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/dfb8c912f1bd11912ec0613164a4ccba375b3a8d))

## 1.0.0 (2026-03-25)

### Features

* add bulk automation management features including selection and actions ([ceec223](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/ceec2237e0c344a568a5e814576d4162ec46306e))
* add HACS validation GitHub Action and hacs.json ([10b1edf](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/10b1edfcbdd295495fe10bbf53378a576297a933))
* Add hard delete functionality for automations and improve payload validation ([3dd70c8](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/3dd70c893b136035350ba536faa43c30932478ad))
* Add hard delete functionality for automations and improve payload validation ([6ca9dd8](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/6ca9dd809dc4a2c05714308185f3f6a9863fa880))
* add Lefthook hooks, ruff config, and GitLab CI pipeline ([7d41bbb](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/7d41bbb92d245586cdcadf4c5cc3eaa4aa86352a))
* add rename_automation WebSocket command ([80296f1](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/80296f1d24b833e7310246e0a636c3998815e044))
* add Selora AI dashboard card ([c07db75](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/c07db759bbc324152e0553b5a01702b876a56967))
* add welcome/onboarding screen for first-time users ([83a4b52](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/83a4b522ff96324f0024379309e9f260677a1d25))
* amber panel refinements and tab navigation ([97e1f7b](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/97e1f7b6aa04286662a6167daffff59213729c5a))
* auto-generate conversation titles ([#7](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/issues/7)) ([f7327be](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/f7327beb8c73d7e65ca6dd6a00b90041abd46d11))
* automated semantic-release versioning + HACS release pipeline ([1f94a0d](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/1f94a0d98eda9850bfa42c4f48f48974cbba30fc))
* automated semantic-release versioning + HACS release pipeline ([94e5051](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/94e50514ab242d69ffc1587b82574c48dfcd89b7))
* automations tab UI refinements — drafts, masonry grid, flowchart toggles ([e4866cd](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/e4866cd562ceaedbb63ccdd0c693266b03fec917))
* chained onboarding flow + dashboard integration + branding ([ddde206](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/ddde2061486fb8998171f96ac7732bb048b8c6eb))
* collect and store device state change history ([299a333](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/299a333e50c10c66633c6d2f33c78671d80261ea))
* collect and store device state change history (Dashboard Card) ([2e166e5](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/2e166e5c95c9b15811ce48c9065b57e637fdc91a))
* complete HACS store readiness ([#15](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/issues/15)) ([bcf6060](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/bcf6060d1b8b41d6af9e5eea4d4b760b27f30527))
* comprehensive panel UX overhaul ([8c7f0e5](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/8c7f0e54df74de35043a1de886d762bddcb89530))
* CSS Grid layout and collapsible sidebar ([7aa1149](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/7aa114963ea298c5454b87548357b3773130864d))
* generate automation suggestions from patterns (Dashboard Card) ([7f75534](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/7f75534755e333e18e56fa8ae0732f14dbad14af))
* hide automation JSON in chat, show generation spinner ([140e62c](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/140e62cc0cf77a7f02902153013109b4d20c6504))
* hide bulk actions behind a Bulk Edit toggle button ([769c2f8](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/769c2f822f58745d02614c8a1d7292f68841d0f9))
* Implement automation lifecycle management with versioning, soft delete,... ([a04c2b0](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/a04c2b0271e2a8530254693f4e62a7e788ce56db))
* improve automation panel trigger and action display ([c1fcebb](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/c1fcebb810de9c57fcc165e99db35b1546c51627))
* Lefthook pre-commit/pre-push hooks + GitLab CI pipeline ([6fa6db4](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/6fa6db4a7397667bc3d1cba810aec3e89f164489))
* LLM response streaming, settings save fix, typing animation ([945f078](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/945f078f6b4e899d7fe8dacce728468f9603bce2))
* OpenAI provider support + dashboard & panel enhancements ([5af1918](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/5af1918ebb8b15c7d9fa55c7302bdf542633bdbd))
* pattern browsing endpoints for automations tab ([4aeabd0](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/4aeabd0267f43e6dd4ead8f8642acc6ceccde1d7))
* Persistent Chat Sessions, Settings Security ([afd2ba9](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/afd2ba9b111fb7200208dc40470660d4e056d334))
* proactive suggestions UI in automations tab ([b23c668](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/b23c66871a3b7a2bcb33daa3b8be21d73514d288))
* proactively surface suggestions on dashboard card ([2bc0dad](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/2bc0dadad85c78407c9434821940418af05cc07f))
* rebrand panel UI with amber accent color ([938c5a8](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/938c5a8e84316cebda0c29dc104bce18c6da35fd))
* replace card buttons with inline Flow / YAML / History tabs ([d51280f](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/d51280fd25bffc2781774f1cac28adc163796914))
* richer state history queries for automations tab ([f66b7d3](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/f66b7d35f50f167554bd887c20d7842b5cb32b32))
* Selora AI MVP — dual LLM backend + auto-create automations ([7dc705e](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/7dc705e7b583ec78243dbcecfdb77057c6d81225))
* Selora MCP server first-release implementation, skill strategy rollout, and security hardening ([3706f54](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/3706f54673d7edb17914830286d21c2a4cbcc756))
* split Automations tab into My Automations / Suggestions sub-tabs ([292ac48](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/292ac48c16d65c8eb24e18d3eb887db9b8588e0d))
* suggestion management endpoints for automations tab ([9a72f10](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/9a72f10d6bc9d65a1ecee8fe2c795b0a4638ca62))
* track dismissed suggestions to prevent re-surfacing rejected patterns ([3831f2f](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/3831f2f6087ff2f63e48d7c4ba5647ae26797426))
* user-friendly trigger and action descriptions in flowchart ([a07ae41](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/a07ae4185fc26418ed9942a20ff1fda2aa683f82))
* wire pattern detection engine (Dashboard Card) ([dfd1712](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/dfd17121753151688297c6f54b4b5ac64bcc9ed2))

### Bug Fixes

* align toggle thumb centered in track ([cda44a8](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/cda44a81cff3e0766a22ec2af264a41890c43bb0))
* automations tab layout — heading, fold order, alignment ([c100c59](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/c100c59dd0a21a6e9bb66eb5ee0a0cecb3bba8d1))
* bump version to 0.2.0 for HACS release ([5219452](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/5219452f8a60e837d7fd39cf265fb5defd50a384))
* bundle lit locally to eliminate external unpkg.com requests ([f60d242](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/f60d2427278c8a6156086da8da2f96cd8fdac7f2))
* coerce non-string trigger values to prevent invalid automations ([f51a592](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/f51a5924cfd04f25e1eb7b67d8c804a492b13732))
* concise LLM, stop button, dedup suggestions, quick create, copy button ([755f964](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/755f96452abe996872d89a32a472876819e19c0c))
* connect Selora AI to HA Assist pipeline ([aad97ed](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/aad97ed51884f5f4a2e2b6270c8d3af6f08bc2cd))
* dashboard card and panel UX fixes ([07ef95f](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/07ef95ff93cb3bac55820e0d654d5277df1f9bd5))
* dashboard card UX and State Management Improvements ([953d07f](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/953d07f7aa01c7ae52f7f6221bdf639cf298158c))
* enforce max 4-word automation aliases in LLM prompts ([94a1d07](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/94a1d07ac95e2502b6eb00b7c778b051e2523eb9))
* include device_tracker and person entities for presence detection ([e003c01](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/e003c015afaacbb26558c603ec8b7706fd7cfd55))
* panel UI polish — badges, YAML editor, active buttons, amber bold ([2158769](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/21587698d82d595ecfb006a70fa11d2ae0671465))
* remove HA conversation agent intercepting chat messages ([2f2ba06](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/2f2ba066dbb25df3ad25558aba737772a39ce8ff))
* remove invalid keys from hacs.json ([6d95614](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/6d95614ae5ddddc193bc267a999d9d05971da85e))
* remove Selora AI Hub device and dashboard mutation ([ddaebb4](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/ddaebb4242a9847030619b1789d6dd855936b717))
* remove stray merge conflict marker in automation_store.py ([4fee1bd](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/4fee1bd62a38a2fc22e53eceff354bfbdb051b2e))
* rename panel title to Selora AI ([6710586](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/6710586fff055b0c9db16be3562a42511adfe48d))
* replace @semantic-release/github with exec script for HACS mirror releases ([a9f8fec](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/a9f8fec7bcca594bb0d0c63b21a1ff5a26072862))
* respect initial_state from suggestion payload ([d9f1f36](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/d9f1f36bbc5d87f63f32384caf58699712148376))
* simplify semantic-release pipeline and remove Python dependency ([7aa3ff4](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/7aa3ff499b2b093aba179ceeea6be5603cbfa21e))
* update default Ollama model to llama4 ([e3bfe71](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/e3bfe719ce54b9fa2d80478cad2c1a613b7e7017))
* update existing automation on refinement instead of creating duplicate ([a73c537](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/a73c5371f099544eff97883b04f66947cedc6137))
* update Ollama defaults for Docker environment ([41859d6](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/41859d65b29baab5a5ca1b5b41187bbcafc0ccd9))
* validate and harden data collection pipeline ([e3bd166](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/e3bd1666c7429527ab757021fd0badbf39c5487d))

### Code Refactoring

* remove hardcoded Android TV/ADB logic ([9886cc2](https://gitlab.com/selorahomes/products/selora-ai/ha-integration/commit/9886cc2694bba1824c7eaed7f20a96054cd44729))

# Changelog

All notable changes to Selora AI are documented here.

This file is generated automatically by [semantic-release](https://semantic-release.gitbook.io/).
Do not edit it manually — your changes will be overwritten on the next release.

<!-- releases below this line are generated automatically -->
