// skycap — capture full-res still(s) from a named AVFoundation camera. Signed .app bundle
// (stable self-signed cert, bundle io.proofsandreasons.skycam) so Sequoia TCC remembers the
// camera grant. Launch via `open -W -a SkyCam.app --args ...`.
//
// Single shot:  --out X.png [--png] [--warmup 1.5]
// BRACKET (one camera session, exposures back-to-back -> tiny inter-frame interval, so clouds
//          barely move between frames): --brk "EXP,/path0.png" --brk "EXP,/path1.png" ...
//          --uvc /path/to/uvc-util [--settle 0.35]  (sets exposure-time-abs via uvc-util between
//          shots on the LIVE session; the caller pre-sets manual mode / gain / WB.)
import AVFoundation
import CoreImage

func err(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

var deviceName = "Elgato Facecam 4K"
var outPath = ""
var warmup = 1.5
var res = "4k"
var listOnly = false
var pngMode = false
var brks: [(String, String)] = []      // (exposure, outpath) for bracket mode
var uvcPath = ""
var settle = 0.35
var a = Array(CommandLine.arguments.dropFirst()); var i = 0
while i < a.count {
  switch a[i] {
  case "--device": i += 1; deviceName = a[i]
  case "--out":    i += 1; outPath = a[i]
  case "--res":    i += 1; res = a[i]
  case "--warmup": i += 1; warmup = Double(a[i]) ?? warmup
  case "--png":    pngMode = true
  case "--list":   listOnly = true
  case "--brk":    i += 1; let p = a[i].split(separator: ",", maxSplits: 1).map(String.init); if p.count == 2 { brks.append((p[0], p[1])) }
  case "--uvc":    i += 1; uvcPath = a[i]
  case "--settle": i += 1; settle = Double(a[i]) ?? settle
  default: err("unknown arg \(a[i])")
  }
  i += 1
}

func dims(_ f: AVCaptureDevice.Format) -> (Int, Int) {
  let d = CMVideoFormatDescriptionGetDimensions(f.formatDescription); return (Int(d.width), Int(d.height))
}
func cameras() -> [AVCaptureDevice] {
  AVCaptureDevice.DiscoverySession(
    deviceTypes: [.external, .builtInWideAngleCamera, .continuityCamera],
    mediaType: .video, position: .unspecified).devices
}

if listOnly {
  for d in cameras() { print("\(d.localizedName)  [\(d.uniqueID)]"); for f in d.formats { let z = dims(f); print("   \(z.0)x\(z.1)") } }
  exit(0)
}
guard !outPath.isEmpty || !brks.isEmpty else { err("need --out or --brk"); exit(64) }

let sem = DispatchSemaphore(value: 0); var granted = false
AVCaptureDevice.requestAccess(for: .video) { ok in granted = ok; sem.signal() }
sem.wait()
guard granted else { err("camera access DENIED by TCC"); exit(3) }
guard let dev = cameras().first(where: { $0.localizedName == deviceName }) ?? cameras().first else { err("no camera found"); exit(4) }

let session = AVCaptureSession(); session.beginConfiguration()
guard let input = try? AVCaptureDeviceInput(device: dev), session.canAddInput(input) else { err("cannot open camera input"); exit(5) }
session.addInput(input)
var preset: AVCaptureSession.Preset = .hd4K3840x2160
switch res { case "1080": preset = .hd1920x1080; case "720": preset = .hd1280x720; default: preset = .hd4K3840x2160 }
if !session.canSetSessionPreset(preset) { preset = session.canSetSessionPreset(.hd4K3840x2160) ? .hd4K3840x2160 : (session.canSetSessionPreset(.hd1920x1080) ? .hd1920x1080 : .high) }
session.sessionPreset = preset
let photoOut = AVCapturePhotoOutput()
guard session.canAddOutput(photoOut) else { err("cannot add photo output"); exit(6) }
session.addOutput(photoOut); session.commitConfiguration(); session.startRunning()

let wantDims: (Int, Int)? = (res == "1080") ? (1920, 1080) : (res == "720") ? (1280, 720) : (3840, 2160)
if let target = dev.formats.first(where: { wantDims != nil && dims($0) == wantDims! }) ?? dev.formats.max(by: { dims($0).0 * dims($0).1 < dims($1).0 * dims($1).1 }) {
  if (try? dev.lockForConfiguration()) != nil {
    session.beginConfiguration(); dev.activeFormat = target
    if #available(macOS 13.0, *), let best = target.supportedMaxPhotoDimensions.max(by: { Int($0.width)*Int($0.height) < Int($1.width)*Int($1.height) }) { photoOut.maxPhotoDimensions = best }
    session.commitConfiguration(); dev.unlockForConfiguration()
  }
}

final class PhotoDelegate: NSObject, AVCapturePhotoCaptureDelegate {
  let done = DispatchSemaphore(value: 0); var data: Data?; var w = 0, h = 0
  func photoOutput(_ o: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
    if let e = error { err("photo error: \(e)") }
    if pngMode, let px = photo.pixelBuffer {
      let ci = CIImage(cvPixelBuffer: px); let ctx = CIContext(options: nil)
      let cs = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
      data = ctx.pngRepresentation(of: ci, format: .RGBA8, colorSpace: cs)
    } else { data = photo.fileDataRepresentation() }
    let d = photo.resolvedSettings.photoDimensions; w = Int(d.width); h = Int(d.height); done.signal()
  }
}
func makeSettings() -> AVCapturePhotoSettings {
  let s = pngMode ? AVCapturePhotoSettings(format: [kCVPixelBufferPixelFormatTypeKey as String: NSNumber(value: kCVPixelFormatType_32BGRA)])
                  : AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
  if #available(macOS 13.0, *) { s.maxPhotoDimensions = photoOut.maxPhotoDimensions }
  return s
}
func captureTo(_ out: String) -> Bool {
  let del = PhotoDelegate()
  photoOut.capturePhoto(with: makeSettings(), delegate: del)
  if del.done.wait(timeout: .now() + 8) == .timedOut { err("capture timeout"); return false }
  guard let d = del.data else { err("no photo data"); return false }
  do { try d.write(to: URL(fileURLWithPath: out)) } catch { err("write failed: \(error)"); return false }
  try? "photo=\(del.w)x\(del.h) fmt=\(pngMode ? "png" : "jpeg")".write(toFile: out + ".meta", atomically: true, encoding: .utf8)
  print("ok \(del.w)x\(del.h) -> \(out)"); return true
}
func uvc(_ args: [String]) {
  guard !uvcPath.isEmpty else { return }
  let p = Process(); p.executableURL = URL(fileURLWithPath: uvcPath); p.arguments = ["-I", "0"] + args
  try? p.run(); p.waitUntilExit()
}
func setExposure(_ e: String) {
  uvc(["-s", "auto-exposure-mode=1"])          // re-assert MANUAL (AVFoundation re-enables auto on the
  uvc(["-s", "exposure-time-abs=\(e)"])        // running session, converging frames toward mid-grey)
}

DispatchQueue.global().async {
  Thread.sleep(forTimeInterval: max(warmup, 0.3))          // one-time camera warmup
  if brks.isEmpty {
    _ = captureTo(outPath)
  } else {
    for (e, out) in brks {                                  // single session, exposures back-to-back
      setExposure(e)
      Thread.sleep(forTimeInterval: max(settle, 0.05))
      _ = captureTo(out)
    }
  }
  session.stopRunning(); exit(0)
}
RunLoop.main.run()
