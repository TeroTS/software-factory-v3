

# Working with snapshots
<a name="microvms-images-snapshots"></a>

When AWS Lambda MicroVMs builds a MicroVM image, it captures a Firecracker snapshot of your fully initialized application. This page describes what a snapshot contains, how to handle uniqueness across MicroVMs that share the same snapshot, and best practices for snapshot-compatible applications.

## What a snapshot captures
<a name="microvms-snapshots-what-is-captured"></a>

A Firecracker snapshot captures the following state from your MicroVM at the moment the image build completes:
+ **Memory state of running processes** – All processes launched by `ENTRYPOINT`/`CMD`, including background daemons, cron jobs, and child processes.
+ **Disk state** – The root filesystem including all files written during the build phase (installed packages, compiled binaries, configuration files).
+ **Network connections and file descriptors** – Any initialized network connections, file handles, pipes, and other descriptors.

When you run a MicroVM, Lambda restores it from this snapshot. Your application resumes exactly where MicroVM image version build left off.

## Compatibility considerations
<a name="microvms-images-snapshots-compatibility"></a>

Because all MicroVMs launched from the same image version share identical initial state, consider the following when building applications with Lambda MicroVMs:
+ **Uniqueness** – If your code generates unique content during image version build phase (unique IDs, secrets, or entropy for pseudorandomness), that content is shared across all MicroVMs run from the same image version. Generate unique content after a new MicroVM starts, not during image build. Use the `/run` lifecycle hook to reset previously generated unique content.
+ **Network connections** – Connections established during the image version build phase might need to be re-established when a MicroVM is run from the image version. Validate connection state and re-establish connections as necessary. In most cases, connections created by AWS SDKs are automatically re-established.

## Handling uniqueness with snapshots
<a name="microvms-images-snapshots-uniqueness"></a>

All MicroVMs run from the same image share identical initial state. To maintain uniqueness across MicroVMs:
+ Generate unique IDs, secrets, and random values after a MicroVM has been started and not during image version build. Use the `/run` lifecycle hook to reset any unique state.
+ Use cryptographically secure pseudorandom number generators (CSPRNGs) for your programming language. Verify that your language-standard cryptographic library receives its entropy from the `/dev/random` device or `/dev/urandom` device. This is default behavior for cryptographic libraries for popular programming languages like Java 11\+ (SecureRandom library), Node.js (crypto.randomBytes library), Python 3.12\+ (Secrets.SystemRandom library), and Dotnet8\+ (Cryptography.RandomNumberGenerator library). Software that reads from `/dev/random` or `/dev/urandom` maintains randomness when used with MicroVM image snapshots.
+ If your application code uses OpenSSL, use the AWS-provided base container image from `public.ecr.aws/lambda/microvms:al2023-minimal` to build your MicroVM image. This image contains an AWS-patched version of OpenSSL that is compatible with snapshotting. If you wish to use your own base image with OpenSSL, use the patched version of OpenSSL (openssl-snapsafe-libs) listed in [the Amazon Linux 2023 package list](https://docs.aws.amazon.com/linux/al2023/release-notes/all-packages.html).