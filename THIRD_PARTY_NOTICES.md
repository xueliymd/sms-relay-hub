# Third-Party Notices

This repository contains optional source-level integration material for
third-party jailbreak projects. Those components remain subject to their
upstream licenses and are not relicensed by the SMS Relay Hub project.

## Ve / VE Enhanced

- Upstream: https://github.com/WingCH/Ve
- Reviewed commit: `a69204defc3e7fb6b14638f32dc8225807655aa9`
- Reviewed version: `2.3.4`
- Upstream license: GNU General Public License version 3
- Local license text: `clients/ve-tweak/COPYING`
- Integration patch: `clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch`

`clients/ve-tweak` contains source that is intended to be built either as a
standalone Theos tweak or merged into the corresponding Ve source tree. The
Ve-derived integration patch is distributed under GPL-3.0. Anyone distributing
a combined binary or modified Ve package must comply with GPL-3.0, including
source-availability obligations.

This notice does not declare the entire SMS Relay Hub repository to be
GPL-3.0. The rest of the repository keeps its existing licensing status.
Consult the upstream project and, where necessary, qualified legal advice
before redistributing a combined package.

## SmsForwarder and Magisk_SmsForwarder

These projects are reviewed as reference implementations only. No source code
from them is vendored into this repository.

- https://github.com/pppscn/SmsForwarder
- https://github.com/zgcwkjOpenProject/Magisk_SmsForwarder

## Trademarks

Apple, iPhone, iOS, Android, Magisk, KernelSU, APatch, Ve, and other names are
trademarks of their respective owners. This project is not affiliated with or
endorsed by those owners.
