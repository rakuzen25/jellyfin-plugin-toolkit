# Changelog

## [0.6.0](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.5.3...v0.6.0) (2026-10-26)


### ⚠ BREAKING CHANGES

* **ci:** remove validator overrides, copied standard impact policy, and the exported setup action; consumers upgrade native bindings directly.

### Features

* **ci:** add native project setup actions ([#25](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/25)) ([1c94000](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/1c940009ff2d1b9c13d40d90e024653e004dee79))
* **ci:** centralize tooling ownership and simplify validation ([#27](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/27)) ([4e240f0](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/4e240f0a317a4931f089b1d2fde47f294961586f))

## [0.5.3](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.5.2...v0.5.3) (2026-10-25)


### Bug Fixes

* **tooling:** use one Python owner and shared dependency automation ([#23](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/23)) ([65acabc](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/65acabc66bb9d01e51543b76b369007be2d3ddd3))

## [0.5.2](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.5.1...v0.5.2) (2026-10-25)


### Features

* **ci:** add scoped GitHub App token action ([#21](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/21)) ([27f6149](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/27f61490cc183093f30cbf1dba50bb299920b23c))


### Bug Fixes

* **ci:** install consumer Python before locked setup ([#19](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/19)) ([445d739](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/445d739ad3015cf9ee5458cadc77317e7b4b10e0))
* **ci:** standardize App setup and recovery guidance ([#22](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/22)) ([1db93b2](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/1db93b238a9f5a792dc845b9c61135cb6e8001ba))

## [0.5.1](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.5.0...v0.5.1) (2026-10-25)


### Bug Fixes

* **ci:** reuse validated artifacts and separate shared checks ([#17](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/17)) ([d5e447b](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/d5e447b3d34c56bca867d3a9bef25ef7b9908904))

## [0.5.0](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.4.3...v0.5.0) (2026-10-24)


### ⚠ BREAKING CHANGES

* **tooling:** share quality policy and enforce strict Python checks ([#16](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/16))

### Features

* **tooling:** share quality policy and enforce strict Python checks ([#16](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/16)) ([7eb4d4f](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/7eb4d4f3d3f2b9dc74d7657d44ba8a4539b07ca3))


### Bug Fixes

* report dependency advisories without blocking validation by default ([#14](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/14)) ([4599f4c](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/4599f4c040db4b4d62f5dbc1cbbf41f6aa15c030))

## [0.4.3](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.4.2...v0.4.3) (2026-10-23)


### Bug Fixes

* standardize dependency maintenance with a shared tooling preset ([#13](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/13)) ([ad2c886](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/ad2c8867657577fdf26c8244e35d0c636bb1bc5c))
* update toolkit dist ([6954d6b](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/6954d6b6f927e30151f12ec4200cc8825f632ac2))

## [0.4.2](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.4.1...v0.4.2) (2026-10-23)


### Bug Fixes

* fix checksum ([bd46ccc](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/bd46ccc8fb972ef119afd9e440207a82ec5c352c))
* fix ci ([15a39f8](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/15a39f8ccf38a2a99101673a3becdbf253e9f3e7))

## [0.4.1](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.4.0...v0.4.1) (2026-10-22)


### Bug Fixes

* update ci ([83829d5](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/83829d5caad6c750b617eb80e8a610c4d70bf0cf))

## [0.4.0](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.3.0...v0.4.0) (2026-10-21)


### ⚠ BREAKING CHANGES

* refactor cli

### Code Refactoring

* refactor cli ([a12296a](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/a12296a48727d8170c8615f86bb2cb23ed3c9692))

## [0.3.0](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.2.0...v0.3.0) (2026-10-21)


### ⚠ BREAKING CHANGES

* refactor toolkit architecture
* refactor tooling

### Code Refactoring

* refactor tooling ([4198dfb](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/4198dfb1779357f219e58b55d2ddeac35c990c9f))
* refactor toolkit architecture ([bc2482f](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/bc2482fe040d199acffb2e56157c9e685051731d))

## [0.2.0](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.1.2...v0.2.0) (2026-10-20)


### ⚠ BREAKING CHANGES

* improve boundaries

### Features

* generate internal project release updates ([fb20f84](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/fb20f846c4fb377ac3f9cfce46ba23788d8eacd6))
* improve boundaries ([2e608ca](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/2e608ca4e1775dde76ec20e1c668880bf4108376))

## [0.1.2](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.1.1...v0.1.2) (2026-10-20)


### Bug Fixes

* update wording ([a648f66](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/a648f66978e3f0d6b2601c673858e2e6e083a1a1))

## [0.1.1](https://github.com/rakuzen25/jellyfin-plugin-toolkit/compare/v0.1.0...v0.1.1) (2026-10-20)


### Features

* add shared scripts ([a367f84](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/a367f84cabed32bc3050c958c6ef733ec1541c5d))

## 0.1.0 (2026-10-19)


### Features

* implement toolkit ([#1](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/1)) ([bc7c41e](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/bc7c41e5a1158cb0ee1ef23bf8a40f0458328417))
* prepare toolkit draft releases with release-please ([#2](https://github.com/rakuzen25/jellyfin-plugin-toolkit/issues/2)) ([ccf35f2](https://github.com/rakuzen25/jellyfin-plugin-toolkit/commit/ccf35f24b2f792e6025df7496c07ff095e9afa9a))
