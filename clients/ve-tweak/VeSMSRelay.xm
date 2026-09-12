#import <Foundation/Foundation.h>
#import "SMSRelayClient.h"

@interface BBBulletin : NSObject
- (NSString *)sectionID;
- (NSString *)title;
- (NSString *)message;
- (NSDate *)date;
@end

@interface BBServer : NSObject
@end

%hook BBServer

- (void)publishBulletin:(BBBulletin *)bulletin destinations:(unsigned long long)destinations {
    %orig;
    if (bulletin == nil ||
        ![bulletin respondsToSelector:@selector(sectionID)] ||
        ![bulletin respondsToSelector:@selector(title)] ||
        ![bulletin respondsToSelector:@selector(message)] ||
        ![bulletin respondsToSelector:@selector(date)]) {
        return;
    }
    id sectionValue = bulletin.sectionID;
    id senderValue = bulletin.title;
    id bodyValue = bulletin.message;
    id dateValue = bulletin.date;
    if (![sectionValue isKindOfClass:[NSString class]] ||
        ![senderValue isKindOfClass:[NSString class]] ||
        ![bodyValue isKindOfClass:[NSString class]] ||
        ![dateValue isKindOfClass:[NSDate class]]) {
        return;
    }
    NSString *sectionID = (NSString *)sectionValue;
    NSString *sender = (NSString *)senderValue;
    NSString *body = (NSString *)bodyValue;
    NSDate *date = (NSDate *)dateValue;
    if (![sectionID isEqualToString:@"com.apple.MobileSMS"]) {
        return;
    }
    [[SMSRelayClient sharedClient] relayMessageFromSender:sender body:body date:date];
}

%end
