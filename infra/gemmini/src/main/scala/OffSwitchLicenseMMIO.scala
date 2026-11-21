package offswitch

import chisel3._
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.bundlebridge.{BundleBridgeSink, BundleBridgeSource}
import org.chipsalliance.diplomacy.lazymodule.{LazyModule, LazyModuleImp}
import freechips.rocketchip.diplomacy.AddressSet
import freechips.rocketchip.regmapper.{RegField, RegWriteFn}
import freechips.rocketchip.resources.SimpleDevice
import freechips.rocketchip.subsystem.SBUS
import freechips.rocketchip.system.{ExampleRocketSystem, ExampleRocketSystemModuleImp}
import freechips.rocketchip.tilelink.{TLFragmenter, TLRegisterNode}

class OffSwitchLicenseMailbox(instanceId: Int) extends BlackBox(Map("INSTANCE_ID" -> instanceId)) {
  override def desiredName = "off_switch_license_mailbox"
  val io = IO(new Bundle {
    val clk = Input(Clock())
    val reset = Input(Bool())
    val submit = Input(Bool())
    val signature = Input(UInt(512.W))
    val nonce = Output(UInt(256.W))
    val status = Output(UInt(32.W))
    val enabled = Output(Bool())
  })
}

class OffSwitchLicenseMMIO(engines: Int, beatBytes: Int)(implicit p: Parameters) extends LazyModule {
  require(engines >= 1 && engines <= 32)
  require(beatBytes == 16)
  val node = TLRegisterNode(
    address = Seq(AddressSet(0x10010000L, 0xfff)),
    device = new SimpleDevice("off-switch-license", Seq("gemmini,off-switch-license")),
    beatBytes = beatBytes
  )
  val grantNode = BundleBridgeSource(() => UInt(engines.W))

  lazy val module = new LazyModuleImp(this) {
    val mailboxes = Seq.tabulate(engines)(index => Module(new OffSwitchLicenseMailbox(index)))
    val mappings = mailboxes.zipWithIndex.flatMap { case (mailbox, index) =>
      val signature = RegInit(0.U(512.W)).suggestName(s"signature_$index")
      val submit = WireDefault(false.B).suggestName(s"submit_$index")
      val command = RegWriteFn((valid, data) => {
        submit := valid && data(0)
        true.B
      })
      val signatureMapping = RegField.bytes(signature).grouped(beatBytes).zipWithIndex.map {
        case (fields, beat) => (0x40 + beat * beatBytes) -> fields
      }.toSeq

      mailbox.io.clk := clock
      mailbox.io.reset := reset.asBool
      mailbox.io.submit := submit
      mailbox.io.signature := signature

      val registers = Seq(
        0x00 -> Seq(RegField.r(32, mailbox.io.status)),
        0x08 -> Seq(RegField.w(1, command)),
        0x20 -> Seq(RegField.r(128, mailbox.io.nonce(127, 0))),
        0x30 -> Seq(RegField.r(128, mailbox.io.nonce(255, 128)))
      ) ++ signatureMapping
      registers.map { case (offset, fields) => (index * 0x80 + offset) -> fields }
    }
    node.regmap(mappings: _*)
    grantNode.bundle := VecInit(mailboxes.map(_.io.enabled)).asUInt
  }
}

class OffSwitchRocketSystem(engines: Int)(implicit p: Parameters) extends ExampleRocketSystem {
  override lazy val desiredName = "ExampleRocketSystem"

  // The default Rocket tile and this peripheral share the SBUS fixed clock.
  private val bus = locateTLBusWrapper(SBUS)
  private val grantDomain = bus.generateSynchronousDomain("OffSwitchGrant")
  private val controller = grantDomain { LazyModule(new OffSwitchLicenseMMIO(engines, bus.beatBytes)) }
  controller.node := bus.coupleTo("off_switch_grant") { TLFragmenter(bus) := _ }
  private val grantSink = BundleBridgeSink[UInt]()
  grantSink := controller.grantNode

  override lazy val module = new ExampleRocketSystemModuleImp(this) {
    val off_switch_grant = IO(Output(UInt(engines.W)))
    off_switch_grant := grantSink.bundle
  }
}
