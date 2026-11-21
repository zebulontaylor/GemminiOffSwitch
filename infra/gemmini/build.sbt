ThisBuild / scalaVersion := "2.13.12"
val chipyard = file(sys.env("GEMMINI_CHIPYARD"))
val checkout = file(sys.env("GEMMINI_CHECKOUT"))
val sources = "src/main/scala"
val chisel = "6.5.0"

// Chipyard pins these generators together. Compile only the projects that
// participate in Gemmini's default Rocket integration.
def generator(name: String, src: File): Project =
  Project(name, file("modules") / name).settings(
    Compile / unmanagedSourceDirectories := Seq(src),
    scalacOptions ++= Seq("-Ymacro-annotations", "-Ytasty-reader"),
    libraryDependencies ++= Seq(
      "org.chipsalliance" %% "chisel" % chisel,
      "org.scala-lang" % "scala-reflect" % scalaVersion.value,
      "com.lihaoyi" %% "sourcecode" % "0.3.1",
      "org.apache.commons" % "commons-text" % "1.9"
    ),
    addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % chisel cross CrossVersion.full)
  )
lazy val cde = generator("cde", chipyard / "tools/cde/cde/src/chipsalliance/rocketchip")
lazy val macros = generator("macros", chipyard / "generators/rocket-chip/macros" / sources)
lazy val diplomacy = generator("diplomacy", chipyard / "generators/diplomacy/diplomacy/src/diplomacy").dependsOn(cde)
lazy val targetutils = generator("targetutils", chipyard / "sims/firesim/sim/midas/targetutils" / sources)
lazy val hardfloat = generator("hardfloat", chipyard / "generators/hardfloat/hardfloat" / sources).dependsOn(targetutils)
lazy val rocket = generator("rocket", chipyard / "generators/rocket-chip" / sources)
  .dependsOn(cde, macros, diplomacy, hardfloat)
  .settings(
    Compile / unmanagedResourceDirectories := Seq(chipyard / "generators/rocket-chip/src/main/resources"),
    libraryDependencies ++= Seq(
      "com.lihaoyi" %% "mainargs" % "0.5.0",
      "org.json4s" %% "json4s-jackson" % "4.0.5",
      "org.scala-graph" %% "graph-core" % "1.13.5"
    )
  )
lazy val gemmini = generator("gemmini", checkout / sources).dependsOn(rocket)
lazy val runner = (project in file(".")).dependsOn(gemmini).settings(
  addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % chisel cross CrossVersion.full),
  Compile / run / fork := true,
  Compile / run / baseDirectory := file(sys.env("GEMMINI_ELAB_OUT")),
  Compile / run / javaOptions ++= Seq("-Xmx8G", "-XX:ActiveProcessorCount=4")
)
