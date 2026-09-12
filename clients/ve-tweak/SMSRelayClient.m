#import "SMSRelayClient.h"

#import <Foundation/Foundation.h>
#import <CoreFoundation/CoreFoundation.h>
#import <CommonCrypto/CommonDigest.h>
#import <CommonCrypto/CommonHMAC.h>
#import <Security/Security.h>
#import <os/log.h>
#import <dispatch/dispatch.h>
#import <math.h>
#import <fcntl.h>
#import <sys/stat.h>
#import <unistd.h>
#import <errno.h>

static NSString * const kSMSRelayPreferencePath = @"/var/mobile/Library/Preferences/com.smsrelay.ve.plist";
static NSString * const kSMSRelayEnabledKey = @"Enabled";
static NSString * const kSMSRelayBaseURLKey = @"BaseURL";
static NSString * const kSMSRelayDeviceIDKey = @"DeviceID";
static NSString * const kSMSRelayHMACSecretKey = @"HMACSecret";
static NSString * const kSMSRelaySource = @"ve_tweak";
static NSString * const kSMSRelayClientVersion = @"ve-tweak-1";
static NSString * const kSMSRelayEndpointPath = @"/api/v1/ingest/messages";
static const NSUInteger kSMSRelayMaxSenderLength = 200;
static const NSUInteger kSMSRelayMaxBodyLength = 4096;
static const NSUInteger kSMSRelayMaxClientMessageIDLength = 200;
static const NSUInteger kSMSRelayMaxPreferenceBytes = 16384;
static const NSTimeInterval kSMSRelayRequestTimeoutSeconds = 15.0;
static const NSTimeInterval kSMSRelayResourceTimeoutSeconds = 30.0;
static const NSUInteger kSMSRelayMaxInFlightTasks = 4;

static os_log_t SMSRelayLog(void) {
    static os_log_t log;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        log = os_log_create("com.smsrelay.ve", "relay");
    });
    return log;
}

static NSString *SMSRelayUTCString(NSDate *date) {
    NSISO8601DateFormatter *formatter = [[NSISO8601DateFormatter alloc] init];
    formatter.formatOptions = NSISO8601DateFormatWithInternetDateTime | NSISO8601DateFormatWithFractionalSeconds;
    formatter.timeZone = [NSTimeZone timeZoneForSecondsFromGMT:0];
    return [formatter stringFromDate:date];
}

static BOOL SMSRelayMatchesBase64URL(NSString *value) {
    if (![value isKindOfClass:[NSString class]] || value.length == 0) {
        return NO;
    }
    NSRegularExpression *expression = [NSRegularExpression regularExpressionWithPattern:@"^[A-Za-z0-9_-]+$" options:0 error:nil];
    NSRange range = NSMakeRange(0, value.length);
    return [expression firstMatchInString:value options:0 range:range] != nil;
}

static NSData *SMSRelayDataFromBase64URL(NSString *value) {
    if (!SMSRelayMatchesBase64URL(value)) {
        return nil;
    }
    NSMutableString *padded = [value mutableCopy];
    [padded replaceOccurrencesOfString:@"-" withString:@"+" options:0 range:NSMakeRange(0, padded.length)];
    [padded replaceOccurrencesOfString:@"_" withString:@"/" options:0 range:NSMakeRange(0, padded.length)];
    NSUInteger remainder = padded.length % 4;
    if (remainder != 0) {
        [padded appendString:[@"===" substringToIndex:(4 - remainder)]];
    }
    return [[NSData alloc] initWithBase64EncodedString:padded options:0];
}

static NSDictionary *SMSRelayReadPreferences(void) {
    int descriptor = open(kSMSRelayPreferencePath.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (descriptor < 0) {
        return nil;
    }

    NSDictionary *preferences = nil;
    struct stat fileInfo;
    if (fstat(descriptor, &fileInfo) != 0 ||
        !S_ISREG(fileInfo.st_mode) ||
        fileInfo.st_uid != geteuid() ||
        (fileInfo.st_mode & (S_IRWXG | S_IRWXO)) != 0 ||
        fileInfo.st_size <= 0 ||
        fileInfo.st_size > (off_t)kSMSRelayMaxPreferenceBytes) {
        close(descriptor);
        return nil;
    }

    NSMutableData *preferenceData = [NSMutableData dataWithCapacity:(NSUInteger)fileInfo.st_size];
    unsigned char buffer[4096];
    while (preferenceData.length <= kSMSRelayMaxPreferenceBytes) {
        ssize_t bytesRead = read(descriptor, buffer, sizeof(buffer));
        if (bytesRead > 0) {
            [preferenceData appendBytes:buffer length:(NSUInteger)bytesRead];
            continue;
        }
        if (bytesRead < 0 && errno == EINTR) {
            continue;
        }
        if (bytesRead < 0) {
            preferenceData = nil;
        }
        break;
    }
    close(descriptor);

    if (preferenceData.length == 0 || preferenceData.length > kSMSRelayMaxPreferenceBytes) {
        return nil;
    }
    NSError *parseError = nil;
    id propertyList = [NSPropertyListSerialization propertyListWithData:preferenceData
                                                                 options:NSPropertyListImmutable
                                                                  format:nil
                                                                   error:&parseError];
    if ([propertyList isKindOfClass:[NSDictionary class]]) {
        preferences = propertyList;
    }
    return preferences;
}

static void SMSRelayAppendLengthPrefixedData(NSMutableData *target, NSData *data) {
    uint64_t length = CFSwapInt64HostToBig((uint64_t)data.length);
    [target appendBytes:&length length:sizeof(length)];
    [target appendData:data];
}

@interface SMSRelayClient () <NSURLSessionDataDelegate>
@property(nonatomic, strong) NSURLSession *session;
@property(nonatomic, strong) NSLock *taskLock;
@property(nonatomic) NSUInteger activeTaskCount;
- (NSString *)base64URLEncodedString:(NSData *)data;
- (NSDictionary *)loadConfiguration;
- (NSString *)stableClientMessageIDForSender:(NSString *)sender
                                        body:(NSString *)body
                                        date:(NSDate *)date;
- (NSString *)sha256HexForData:(NSData *)data;
- (BOOL)reserveTaskSlot;
- (void)releaseTaskSlot;
@end

@implementation SMSRelayClient

+ (instancetype)sharedClient {
    static SMSRelayClient *client;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        client = [[self alloc] init];
    });
    return client;
}

- (instancetype)init {
    self = [super init];
    if (self != nil) {
        _taskLock = [[NSLock alloc] init];
        NSURLSessionConfiguration *sessionConfiguration = [NSURLSessionConfiguration ephemeralSessionConfiguration];
        sessionConfiguration.URLCache = nil;
        sessionConfiguration.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
        sessionConfiguration.HTTPShouldSetCookies = NO;
        sessionConfiguration.timeoutIntervalForRequest = kSMSRelayRequestTimeoutSeconds;
        sessionConfiguration.timeoutIntervalForResource = kSMSRelayResourceTimeoutSeconds;
        NSOperationQueue *delegateQueue = [[NSOperationQueue alloc] init];
        delegateQueue.maxConcurrentOperationCount = 1;
        _session = [NSURLSession sessionWithConfiguration:sessionConfiguration delegate:self delegateQueue:delegateQueue];
    }
    return self;
}

- (BOOL)reserveTaskSlot {
    [self.taskLock lock];
    if (self.activeTaskCount >= kSMSRelayMaxInFlightTasks) {
        [self.taskLock unlock];
        return NO;
    }
    self.activeTaskCount++;
    [self.taskLock unlock];
    return YES;
}

- (void)releaseTaskSlot {
    [self.taskLock lock];
    if (self.activeTaskCount > 0) {
        self.activeTaskCount--;
    }
    [self.taskLock unlock];
}

- (NSString *)base64URLEncodedString:(NSData *)data {
    NSString *encoded = [data base64EncodedStringWithOptions:0];
    encoded = [encoded stringByReplacingOccurrencesOfString:@"+" withString:@"-"];
    encoded = [encoded stringByReplacingOccurrencesOfString:@"/" withString:@"_"];
    return [encoded stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"="]];
}

- (NSString *)sha256HexForData:(NSData *)data {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *hex = [NSMutableString stringWithCapacity:CC_SHA256_DIGEST_LENGTH * 2];
    for (NSUInteger index = 0; index < CC_SHA256_DIGEST_LENGTH; index++) {
        [hex appendFormat:@"%02x", digest[index]];
    }
    return hex;
}

- (NSDictionary *)loadConfiguration {
    NSDictionary *preferences = SMSRelayReadPreferences();
    id enabled = preferences[kSMSRelayEnabledKey];
    id baseURLValue = preferences[kSMSRelayBaseURLKey];
    id deviceIDValue = preferences[kSMSRelayDeviceIDKey];
    id secretValue = preferences[kSMSRelayHMACSecretKey];
    if (![preferences isKindOfClass:[NSDictionary class]] ||
        ![enabled isKindOfClass:[NSNumber class]] ||
        CFGetTypeID((__bridge CFTypeRef)enabled) != CFBooleanGetTypeID() ||
        !enabled.boolValue ||
        ![baseURLValue isKindOfClass:[NSString class]] ||
        ![deviceIDValue isKindOfClass:[NSString class]] ||
        ![secretValue isKindOfClass:[NSString class]]) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }

    NSString *baseURLString = (NSString *)baseURLValue;
    NSURLComponents *components = [NSURLComponents componentsWithString:baseURLString];
    if (components == nil || [components.scheme caseInsensitiveCompare:@"https"] != NSOrderedSame ||
        components.host.length == 0 || components.user != nil || components.password != nil ||
        components.query != nil || components.fragment != nil) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }

    NSString *deviceID = (NSString *)deviceIDValue;
    NSUUID *deviceUUID = [[NSUUID alloc] initWithUUIDString:deviceID];
    if (deviceUUID == nil) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }

    NSString *secret = (NSString *)secretValue;
    if (!SMSRelayMatchesBase64URL(secret)) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }
    NSString *secretLower = [secret lowercaseString];
    NSSet *rawSecretSentinels = [NSSet setWithArray:@[@"placeholder", @"change_me", @"changeme", @"replace_me", @"replace-me", @"your_secret", @"your-secret", @"not_enrolled", @"todo", @"xxx"]];
    if ([rawSecretSentinels containsObject:secretLower]) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }
    NSData *secretData = SMSRelayDataFromBase64URL(secret);
    if (secretData == nil || secretData.length != 32 ||
        ![[self base64URLEncodedString:secretData] isEqualToString:secret]) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }

    const unsigned char *secretBytes = secretData.bytes;
    BOOL allZero = YES;
    BOOL allSame = YES;
    unsigned char firstByte = secretBytes[0];
    for (NSUInteger index = 0; index < secretData.length; index++) {
        if (secretBytes[index] != 0) {
            allZero = NO;
        }
        if (secretBytes[index] != firstByte) {
            allSame = NO;
        }
    }
    if (allZero || allSame) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }

    NSString *decodedText = [[NSString alloc] initWithData:secretData encoding:NSUTF8StringEncoding];
    if (decodedText != nil) {
        NSString *decodedLower = [decodedText lowercaseString];
        NSArray *decodedMarkers = @[@"placeholder", @"change_me", @"changeme", @"replace_me", @"replace-me", @"your_secret", @"your-secret", @"not_enrolled", @"todo", @"xxx"];
        for (NSString *marker in decodedMarkers) {
            if ([decodedLower containsString:marker]) {
                os_log_error(SMSRelayLog(), "relay_config_invalid");
                return nil;
            }
        }
    }

    NSMutableString *path = [components.path mutableCopy];
    if (path == nil) {
        path = [NSMutableString string];
    }
    while (path.length > 0 && [path hasSuffix:@"/"]) {
        [path deleteCharactersInRange:NSMakeRange(path.length - 1, 1)];
    }
    components.path = [path stringByAppendingString:kSMSRelayEndpointPath];
    NSURL *endpoint = components.URL;
    if (endpoint == nil) {
        os_log_error(SMSRelayLog(), "relay_config_invalid");
        return nil;
    }
    return @{ @"endpoint": endpoint,
              @"deviceID": deviceUUID.UUIDString,
              @"secretData": secretData };
}

- (NSString *)stableClientMessageIDForSender:(NSString *)sender
                                        body:(NSString *)body
                                        date:(NSDate *)date {
    NSData *senderData = [sender dataUsingEncoding:NSUTF8StringEncoding];
    NSData *bodyData = [body dataUsingEncoding:NSUTF8StringEncoding];
    NSString *dateString = SMSRelayUTCString(date);
    NSData *dateData = [dateString dataUsingEncoding:NSUTF8StringEncoding];
    if (senderData == nil || bodyData == nil || dateData == nil) {
        return nil;
    }
    NSMutableData *identityData = [NSMutableData data];
    SMSRelayAppendLengthPrefixedData(identityData, senderData);
    SMSRelayAppendLengthPrefixedData(identityData, bodyData);
    SMSRelayAppendLengthPrefixedData(identityData, dateData);
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(identityData.bytes, (CC_LONG)identityData.length, digest);
    NSMutableString *hex = [NSMutableString stringWithCapacity:CC_SHA256_DIGEST_LENGTH * 2];
    for (NSUInteger index = 0; index < CC_SHA256_DIGEST_LENGTH; index++) {
        [hex appendFormat:@"%02x", digest[index]];
    }
    return [@"ve_" stringByAppendingString:hex];
}

- (void)URLSession:(NSURLSession *)session
              task:(NSURLSessionTask *)task
willPerformHTTPRedirection:(NSHTTPURLResponse *)response
        newRequest:(NSURLRequest *)request
 completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil);
}

- (void)URLSession:(NSURLSession *)session
          dataTask:(NSURLSessionDataTask *)dataTask
didReceiveResponse:(NSURLResponse *)response
 completionHandler:(void (^)(NSURLSessionResponseDisposition disposition))completionHandler {
    dataTask.taskDescription = @"response_seen";
    if ([response isKindOfClass:[NSHTTPURLResponse class]]) {
        NSInteger statusCode = [(NSHTTPURLResponse *)response statusCode];
        os_log_info(SMSRelayLog(), "relay_http_status=%{public}ld", (long)statusCode);
    } else {
        os_log_error(SMSRelayLog(), "relay_transport_failed");
    }
    completionHandler(NSURLSessionResponseCancel);
}

- (void)URLSession:(NSURLSession *)session
              task:(NSURLSessionTask *)task
didCompleteWithError:(__unused NSError *)error {
    if (![task.taskDescription isEqualToString:@"response_seen"]) {
        os_log_error(SMSRelayLog(), "relay_transport_failed");
    }
    [self releaseTaskSlot];
}

- (void)relayMessageFromSender:(NSString *)sender
                           body:(NSString *)body
                           date:(NSDate *)date {
    if (![sender isKindOfClass:[NSString class]] || sender.length == 0 || sender.length > kSMSRelayMaxSenderLength ||
        ![body isKindOfClass:[NSString class]] || body.length == 0 || body.length > kSMSRelayMaxBodyLength ||
        ![date isKindOfClass:[NSDate class]]) {
        os_log_error(SMSRelayLog(), "relay_input_invalid");
        return;
    }

    NSDictionary *configuration = [self loadConfiguration];
    if (configuration == nil) {
        return;
    }

    NSString *receivedAt = SMSRelayUTCString(date);
    if (receivedAt.length == 0) {
        os_log_error(SMSRelayLog(), "relay_input_invalid");
        return;
    }
    NSString *clientMessageID = [self stableClientMessageIDForSender:sender body:body date:date];
    if (clientMessageID.length == 0 || clientMessageID.length > kSMSRelayMaxClientMessageIDLength) {
        os_log_error(SMSRelayLog(), "relay_input_invalid");
        return;
    }

    NSDictionary *payload = @{
        @"schema_version": @1,
        @"client_message_id": clientMessageID,
        @"received_at": receivedAt,
        @"sender": sender,
        @"body": body,
        @"source": kSMSRelaySource,
        @"metadata": @{ @"client_version": kSMSRelayClientVersion }
    };
    NSData *rawBody = [NSJSONSerialization dataWithJSONObject:payload options:NSJSONWritingSortedKeys error:nil];
    if (rawBody == nil) {
        os_log_error(SMSRelayLog(), "relay_serialization_failed");
        return;
    }

    long long timestampValue = (long long)floor([[NSDate date] timeIntervalSince1970]);
    NSString *timestamp = [NSString stringWithFormat:@"%lld", timestampValue];
    uint8_t nonceBytes[16];
    if (SecRandomCopyBytes(kSecRandomDefault, sizeof(nonceBytes), nonceBytes) != errSecSuccess) {
        os_log_error(SMSRelayLog(), "relay_nonce_failed");
        return;
    }
    NSData *nonceData = [NSData dataWithBytes:nonceBytes length:sizeof(nonceBytes)];
    NSString *nonce = [self base64URLEncodedString:nonceData];
    NSString *bodyHash = [self sha256HexForData:rawBody];
    NSString *canonical = [@[timestamp, nonce, bodyHash] componentsJoinedByString:@"\n"];
    NSData *canonicalData = [canonical dataUsingEncoding:NSUTF8StringEncoding];
    unsigned char hmac[CC_SHA256_DIGEST_LENGTH];
    NSData *secretData = configuration[@"secretData"];
    CCHmac(kCCHmacAlgSHA256, secretData.bytes, secretData.length, canonicalData.bytes, canonicalData.length, hmac);
    NSString *signature = [self base64URLEncodedString:[NSData dataWithBytes:hmac length:sizeof(hmac)]];

    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:configuration[@"endpoint"]];
    request.HTTPMethod = @"POST";
    request.HTTPBody = rawBody;
    request.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    [request setValue:configuration[@"deviceID"] forHTTPHeaderField:@"X-Device-Id"];
    [request setValue:timestamp forHTTPHeaderField:@"X-Timestamp"];
    [request setValue:nonce forHTTPHeaderField:@"X-Nonce"];
    [request setValue:signature forHTTPHeaderField:@"X-Signature"];
    [request setValue:clientMessageID forHTTPHeaderField:@"X-Idempotency-Key"];

    if (![self reserveTaskSlot]) {
        os_log_error(SMSRelayLog(), "relay_busy");
        return;
    }
    NSURLSessionDataTask *task = [self.session dataTaskWithRequest:request];
    if (task == nil) {
        [self releaseTaskSlot];
        os_log_error(SMSRelayLog(), "relay_transport_failed");
        return;
    }
    [task resume];
}

@end
