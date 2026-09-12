ARCHS = arm64 arm64e
TARGET = iphone:clang:latest:14.0
INSTALL_TARGET_PROCESSES = SpringBoard

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = VeSMSRelay
VeSMSRelay_FILES = VeSMSRelay.xm SMSRelayClient.m
VeSMSRelay_CFLAGS = -fobjc-arc
VeSMSRelay_FRAMEWORKS = Foundation Security

include $(THEOS_MAKE_PATH)/tweak.mk
