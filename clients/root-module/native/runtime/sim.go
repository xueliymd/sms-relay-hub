package main

type simProfile struct {
	SlotIndex      *int
	SIMID          *int
	SubscriptionID *int
	PhoneNumber    string
	CarrierName    string
}

type simSnapshot struct {
	SlotIndex      *int
	SIMID          *int
	SubscriptionID *int
	PhoneNumber    string
	CarrierName    string
}

func chooseSIMSnapshot(profiles []simProfile, subscriptionID *int, simID *int, slotIndex *int) (simSnapshot, bool) {
	match := func(profile simProfile) bool { return false }
	if subscriptionID != nil {
		match = func(profile simProfile) bool {
			return profile.SubscriptionID != nil && *profile.SubscriptionID == *subscriptionID
		}
		if snapshot, ok := selectSIMMatches(profiles, match); ok {
			return snapshot, true
		}
	}
	if simID != nil {
		match = func(profile simProfile) bool {
			return profile.SIMID != nil && *profile.SIMID == *simID
		}
		if snapshot, ok := selectSIMMatches(profiles, match); ok {
			return snapshot, true
		}
		// Several vendor SMS providers store the Android subscription ID in a
		// column named sim_id. Accept it only when it uniquely matches an active
		// subscription profile.
		match = func(profile simProfile) bool {
			return profile.SubscriptionID != nil && *profile.SubscriptionID == *simID
		}
		if snapshot, ok := selectSIMMatches(profiles, match); ok {
			return snapshot, true
		}
		// Older ROMs use sim_id as a zero-based physical slot.
		match = func(profile simProfile) bool {
			return profile.SlotIndex != nil && *profile.SlotIndex == *simID
		}
		if snapshot, ok := selectSIMMatches(profiles, match); ok {
			return snapshot, true
		}
	}
	if slotIndex != nil {
		match = func(profile simProfile) bool {
			return profile.SlotIndex != nil && *profile.SlotIndex == *slotIndex
		}
		if snapshot, ok := selectSIMMatches(profiles, match); ok {
			return snapshot, true
		}
	}
	return simSnapshot{}, false
}

func selectSIMMatches(profiles []simProfile, match func(simProfile) bool) (simSnapshot, bool) {
	var selected *simProfile
	phone := ""
	for index := range profiles {
		profile := profiles[index]
		if !match(profile) {
			continue
		}
		if profile.PhoneNumber != "" {
			if phone != "" && phone != profile.PhoneNumber {
				return simSnapshot{}, false
			}
			phone = profile.PhoneNumber
		}
		if selected == nil || (selected.PhoneNumber == "" && profile.PhoneNumber != "") {
			copy := profile
			selected = &copy
		}
	}
	if selected == nil {
		return simSnapshot{}, false
	}
	return simSnapshot{
		SlotIndex:      selected.SlotIndex,
		SIMID:          selected.SIMID,
		SubscriptionID: selected.SubscriptionID,
		PhoneNumber:    phone,
		CarrierName:    selected.CarrierName,
	}, true
}
