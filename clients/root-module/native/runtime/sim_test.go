package main

import "testing"

func TestChooseSIMSnapshotPrefersSubscriptionHint(t *testing.T) {
	profiles := []simProfile{
		{SlotIndex: intPtr(0), SubscriptionID: intPtr(5), PhoneNumber: "+15550100005", CarrierName: "carrier-5"},
		{SlotIndex: intPtr(1), SubscriptionID: intPtr(6), PhoneNumber: "+15550100006", CarrierName: "carrier-6"},
	}
	got, ok := chooseSIMSnapshot(profiles, intPtr(5), nil, intPtr(1))
	if !ok || got.PhoneNumber != "+15550100005" || got.SlotIndex == nil || *got.SlotIndex != 0 {
		t.Fatalf("snapshot=%+v ok=%v", got, ok)
	}
}

func TestChooseSIMSnapshotMarksConflictingPhoneNumbersUnresolved(t *testing.T) {
	profiles := []simProfile{
		{SubscriptionID: intPtr(5), PhoneNumber: "+15550100005"},
		{SubscriptionID: intPtr(5), PhoneNumber: "+15550109999"},
	}
	if _, ok := chooseSIMSnapshot(profiles, intPtr(5), nil, nil); ok {
		t.Fatal("conflicting subscription profiles resolved")
	}
}

func TestChooseSIMSnapshotDoesNotInventEmptyPhoneNumber(t *testing.T) {
	profiles := []simProfile{
		{SlotIndex: intPtr(0), SubscriptionID: intPtr(5), CarrierName: "carrier-5"},
	}
	got, ok := chooseSIMSnapshot(profiles, intPtr(5), nil, nil)
	if !ok || got.PhoneNumber != "" || got.CarrierName != "carrier-5" {
		t.Fatalf("snapshot=%+v ok=%v", got, ok)
	}
}

func TestChooseSIMSnapshotTreatsROMsimIDAsSubscriptionID(t *testing.T) {
	profiles := []simProfile{
		{SlotIndex: intPtr(0), SubscriptionID: intPtr(5), CarrierName: "carrier-5"},
		{SlotIndex: intPtr(1), SubscriptionID: intPtr(6), CarrierName: "carrier-6"},
	}
	got, ok := chooseSIMSnapshot(profiles, nil, intPtr(6), nil)
	if !ok || got.SlotIndex == nil || *got.SlotIndex != 1 || got.SubscriptionID == nil || *got.SubscriptionID != 6 {
		t.Fatalf("snapshot=%+v ok=%v", got, ok)
	}
}

func TestParseDumpsysSubscriptionProfilesSupportsSubscriptionInfoInternal(t *testing.T) {
	raw := []byte(`
  [SubscriptionInfoInternal: id=2 iccId=redacted simSlotIndex=1 carrierName=中国联通 number=[****]]
  [SubscriptionInfoInternal: id=3 iccId=redacted simSlotIndex=0 carrierName=中国电信 number=[****]]
`)
	profiles := parseDumpsysSubscriptionProfiles(raw)
	if len(profiles) != 2 {
		t.Fatalf("profiles=%+v", profiles)
	}
	if *profiles[0].SlotIndex != 0 || *profiles[0].SubscriptionID != 3 || profiles[0].CarrierName != "中国电信" {
		t.Fatalf("slot0=%+v", profiles[0])
	}
	if *profiles[1].SlotIndex != 1 || *profiles[1].SubscriptionID != 2 || profiles[1].CarrierName != "中国联通" {
		t.Fatalf("slot1=%+v", profiles[1])
	}
}

func intPtr(value int) *int { return &value }
