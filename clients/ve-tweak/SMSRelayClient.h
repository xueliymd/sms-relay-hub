#import <Foundation/Foundation.h>

@interface SMSRelayClient : NSObject

+ (instancetype)sharedClient;

- (void)relayMessageFromSender:(NSString *)sender
                           body:(NSString *)body
                           date:(NSDate *)date;

@end
