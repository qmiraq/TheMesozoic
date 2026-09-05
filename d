 .gitignore                                         |   10 [32m+[m[31m-[m
 Config/point-management.json                       |   24 [32m+[m
 auto_restart_state.json                            |    1 [32m+[m
 bot/assets/Gateway-region-mask.png                 |  Bin [31m0[m -> [32m104723[m bytes
 bot/assets/V7-0-Gateway.png                        |  Bin [31m0[m -> [32m2913218[m bytes
 bot/assets/V8-0-Gateway1600BW.png                  |  Bin [31m0[m -> [32m1803410[m bytes
 bot/assets/killfeed-test.png                       |  Bin [31m0[m -> [32m284235[m bytes
 bot/cogs/admin_logs.py                             |  813 [32m+++++++++[m
 bot/cogs/auto_restart.py                           |  589 [32m++++++[m
 bot/cogs/dino_storage.py                           | 1879 [32m++++++++++++++++++++[m
 bot/cogs/economy.py                                | 1734 [32m++++++++++++++++++[m
 bot/cogs/player.py                                 |  649 [32m++++++[m[31m-[m
 bot/cogs/population_control.py                     |  362 [32m++++[m
 bot/cogs/weather_forecast.py                       |  137 [32m++[m
 bot/services/activity_map_service.py               |  705 [32m++++++++[m
 bot/services/admin_command_tree.py                 |   28 [32m+[m
 bot/services/admin_log_service.py                  |  598 [32m+++++++[m
 bot/services/dino_storage_service.py               | 1455 [32m+++++++++++++++[m
 bot/services/dinosaur_render_service.py            |  104 [32m++[m
 bot/services/economy_service.py                    | 1556 [32m++++++++++++++++[m
 bot/services/head_staff_management_bridge.py       |   40 [32m+[m
 bot/services/player_panel_service.py               |   76 [32m+[m
 bot/services/point_management_service.py           |   71 [32m+[m
 bot/services/population_control_service.py         |  930 [32m++++++++++[m
 bot/services/rcon_api_service.py                   |    2 [32m+[m[31m-[m
 bot/services/weather_forecast_service.py           |  120 [32m++[m
 main.py                                            |   23 [32m+[m[31m-[m
 miniEniac-RCON/Config/mesozoic-ai-admins.json      |   10 [32m+[m
 .../Controllers/AutoRestartController.cs           |  143 [32m++[m
 .../Controllers/HeadStaffManagementController.cs   |   53 [32m+[m
 .../Controllers/MesozoicAiAdminController.cs       | 1048 [32m+++++++++++[m
 .../Controllers/PointManagementController.cs       |  195 [32m++[m
 .../Controllers/PopulationControlController.cs     |   63 [32m+[m
 .../Controllers/WebPanelTestController.cs          |  116 [32m++[m
 miniEniac-RCON/Endpoints/SkinPresetEndpoints.cs    | 1166 [32m++++++++++++[m
 miniEniac-RCON/Program.cs                          |   18 [32m+[m[31m-[m
 miniEniac-RCON/WebPanelTest/LogoMeso.png           |  Bin [31m0[m -> [32m231341[m bytes
 miniEniac-RCON/WebPanelTest/app.js                 |  345 [32m++++[m
 miniEniac-RCON/WebPanelTest/index.html             |  154 [32m++[m
 miniEniac-RCON/WebPanelTest/styles.css             |  198 [32m+++[m
 miniEniac-RCON/appsettings.Production.json         |   10 [32m+[m
 miniEniac-RCON/appsettings.json                    |   66 [32m+[m[31m-[m
 .../bin/Debug/net10.0/Microsoft.OpenApi.dll        |  Bin [31m604984[m -> [32m0[m bytes
 .../bin/Debug/net10.0/TheIsleEvrimaRconClient.dll  |  Bin [31m32768[m -> [32m0[m bytes
 .../bin/Debug/net10.0/appsettings.Development.json |    8 [31m-[m
 miniEniac-RCON/bin/Debug/net10.0/appsettings.json  |   14 [31m-[m
 .../bin/Debug/net10.0/miniEniac-RCON.deps.json     |   57 [31m-[m
 .../bin/Debug/net10.0/miniEniac-RCON.dll           |  Bin [31m23040[m -> [32m0[m bytes
 .../bin/Debug/net10.0/miniEniac-RCON.exe           |  Bin [31m162304[m -> [32m0[m bytes
 .../bin/Debug/net10.0/miniEniac-RCON.pdb           |  Bin [31m26260[m -> [32m0[m bytes
 .../net10.0/miniEniac-RCON.runtimeconfig.json      |   19 [31m-[m
 .../miniEniac-RCON.staticwebassets.endpoints.json  |    1 [31m-[m
 miniEniac-RCON/miniEniac-RCON.csproj               |    1 [32m+[m
 ....NETCoreApp,Version=v10.0.AssemblyAttributes.cs |    4 [31m-[m
 miniEniac-RCON/obj/Debug/net10.0/ApiEndpoints.json |   22 [31m-[m
 miniEniac-RCON/obj/Debug/net10.0/apphost.exe       |  Bin [31m162304[m -> [32m0[m bytes
 .../obj/Debug/net10.0/miniEnia.0A011E06.Up2Date    |    0
 .../Debug/net10.0/miniEniac-RCON.AssemblyInfo.cs   |   23 [31m-[m
 .../miniEniac-RCON.AssemblyInfoInputs.cache        |    1 [31m-[m
 ...-RCON.GeneratedMSBuildEditorConfig.editorconfig |   24 [31m-[m
 .../Debug/net10.0/miniEniac-RCON.GlobalUsings.g.cs |   17 [31m-[m
 ...niac-RCON.MvcApplicationPartsAssemblyInfo.cache |    0
 .../obj/Debug/net10.0/miniEniac-RCON.assets.cache  |  Bin [31m1775[m -> [32m0[m bytes
 .../miniEniac-RCON.csproj.AssemblyReference.cache  |  Bin [31m956[m -> [32m0[m bytes
 .../miniEniac-RCON.csproj.CoreCompileInputs.cache  |    1 [31m-[m
 .../miniEniac-RCON.csproj.FileListAbsolute.txt     |   33 [31m-[m
 .../obj/Debug/net10.0/miniEniac-RCON.dll           |  Bin [31m23040[m -> [32m0[m bytes
 .../net10.0/miniEniac-RCON.genruntimeconfig.cache  |    1 [31m-[m
 .../obj/Debug/net10.0/miniEniac-RCON.pdb           |  Bin [31m26260[m -> [32m0[m bytes
 .../obj/Debug/net10.0/ref/miniEniac-RCON.dll       |  Bin [31m11264[m -> [32m0[m bytes
 .../obj/Debug/net10.0/refint/miniEniac-RCON.dll    |  Bin [31m11264[m -> [32m0[m bytes
 .../obj/Debug/net10.0/rjsmcshtml.dswa.cache.json   |    1 [31m-[m
 .../obj/Debug/net10.0/rjsmrazor.dswa.cache.json    |    1 [31m-[m
 .../obj/Debug/net10.0/rpswa.dswa.cache.json        |    1 [31m-[m
 .../net10.0/staticwebassets.build.endpoints.json   |    1 [31m-[m
 .../obj/Debug/net10.0/staticwebassets.build.json   |    1 [31m-[m
 .../Debug/net10.0/staticwebassets.build.json.cache |    1 [31m-[m
 .../staticwebassets.references.upToDateCheck.txt   |    0
 .../obj/Debug/net10.0/staticwebassets.removed.txt  |    0
 .../obj/Debug/net10.0/swae.build.ex.cache          |    0
 ....NETCoreApp,Version=v10.0.AssemblyAttributes.cs |    4 [31m-[m
 .../Release/net10.0/miniEniac-RCON.AssemblyInfo.cs |   23 [31m-[m
 .../miniEniac-RCON.AssemblyInfoInputs.cache        |    1 [31m-[m
 ...-RCON.GeneratedMSBuildEditorConfig.editorconfig |   24 [31m-[m
 .../net10.0/miniEniac-RCON.GlobalUsings.g.cs       |   17 [31m-[m
 .../Release/net10.0/miniEniac-RCON.assets.cache    |  Bin [31m1775[m -> [32m0[m bytes
 .../miniEniac-RCON.csproj.AssemblyReference.cache  |  Bin [31m956[m -> [32m0[m bytes
 .../obj/miniEniac-RCON.csproj.nuget.dgspec.json    |  502 [31m------[m
 .../obj/miniEniac-RCON.csproj.nuget.g.props        |   16 [31m-[m
 .../obj/miniEniac-RCON.csproj.nuget.g.targets      |    2 [31m-[m
 miniEniac-RCON/obj/project.assets.json             |  570 [31m------[m
 miniEniac-RCON/obj/project.nuget.cache             |   11 [31m-[m
 miniEniac-RCON/wwwroot/admin-ai/Gateway.png        |  Bin [31m0[m -> [32m3456651[m bytes
 miniEniac-RCON/wwwroot/admin-ai/index.html         | 1282 [32m+++++++++++++[m
 miniEniac-RCON/wwwroot/app.js                      | 1730 [32m+++++++++++++++++[m[31m-[m
 .../wwwroot/backup-v9-20260716-004959/app.js       |  573 [32m++++++[m
 .../wwwroot/backup-v9-20260716-004959/index.html   |  154 [32m++[m
 .../wwwroot/backup-v9-20260716-004959/styles.css   |  646 [32m+++++++[m
 miniEniac-RCON/wwwroot/favicon.svg                 |   12 [32m+[m
 miniEniac-RCON/wwwroot/index.html                  |  146 [32m+[m[31m-[m
 miniEniac-RCON/wwwroot/styles.css                  | 1103 [32m++++++++++[m[31m--[m
 miniEniac-current-website-source.zip               |  Bin [31m0[m -> [32m52784[m bytes
 population_control_initial_Game.ini                |  125 [32m++[m
 query                                              |    1 [32m+[m
 restart-all.bat                                    |   13 [32m+[m
 server_status_message.json                         |    1 [32m+[m
 update.bat                                         |   80 [32m+[m
 weather_forecast_panel.json                        |    1 [32m+[m
 108 files changed, 21040 insertions(+), 1720 deletions(-)
