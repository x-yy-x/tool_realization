#!/bin/sh
mkfs.vfat -F 32 -n OHOS "$IMG"
mformat -i "$IMG" ::
mcopy -i "$IMG" rootfs/* ::
