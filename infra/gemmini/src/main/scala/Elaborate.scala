package offswitch

import circt.stage.ChiselStage
import org.chipsalliance.cde.config.{Config, Parameters}
import freechips.rocketchip.devices.tilelink.BootROMLocated
import freechips.rocketchip.diplomacy.LazyModule
import freechips.rocketchip.rocket.WithNHugeCores
import freechips.rocketchip.subsystem.{HasTilesExternalResetVectorKey, SystemBusKey, WithCoherentBusTopology}
import freechips.rocketchip.system.{BaseConfig, ExampleRocketSystem}

object Elaborate extends App {
  val host = new Config((site, here, up) => {
    // The host supplies bus parameters. Its boot ROM is outside the analysis top.
    case BootROMLocated(_) => None
    case HasTilesExternalResetVectorKey => true
    case SystemBusKey => up(SystemBusKey, site).copy(beatBytes = 16)
  })
  implicit val parameters: Parameters = new gemmini.DefaultGemminiConfig(gemmini.GemminiConfigs.defaultConfig) ++
    new WithNHugeCores(1) ++ host ++ new WithCoherentBusTopology ++ new BaseConfig

  ChiselStage.emitSystemVerilogFile(
    sys.env.get("GEMMINI_LICENSE_ENGINES") match {
      case Some(count) => LazyModule(new OffSwitchRocketSystem(count.toInt)).module
      case None => LazyModule(new ExampleRocketSystem).module
    },
    Array("--target-dir", sys.env("GEMMINI_ELAB_OUT"), "--split-verilog",
          "--firtool-binary-path", sys.env("GEMMINI_FIRTOOL")),
    Array("--disable-all-randomization", "--disable-annotation-unknown", "--strip-debug-info")
  )
}
