using System.Diagnostics;
using System.Security.Claims;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace miniEniac_RCON.Controllers;

[Authorize, ApiController, Route("test/api/headstaff")]
public sealed class HeadStaffManagementController : ControllerBase
{
    private readonly IWebHostEnvironment _environment;
    public HeadStaffManagementController(IWebHostEnvironment environment) => _environment = environment;

    [HttpPost("execute")]
    public async Task<IActionResult> Execute([FromBody] JsonElement request, CancellationToken token)
    {
        if (!await IsHeadStaff(token)) return Forbid();
        var values = JsonSerializer.Deserialize<Dictionary<string, object?>>(request.GetRawText()) ?? new();
        values["actor"] = User.FindFirstValue(ClaimTypes.NameIdentifier) ?? "website";
        string root = Path.GetFullPath(Path.Combine(_environment.ContentRootPath, ".."));
        string script = Path.Combine(root, "bot", "services", "head_staff_management_bridge.py");
        if (!System.IO.File.Exists(script)) return StatusCode(503, new { error = "Management bridge is not installed." });
        string[] candidates = {
            Path.Combine(root, ".venv", "Scripts", "python.exe"),
            Path.Combine(root, "venv", "Scripts", "python.exe"), "python.exe", "py.exe"
        };
        string python = candidates.FirstOrDefault(System.IO.File.Exists) ?? "python.exe";
        var start = new ProcessStartInfo(python, $"\"{script}\"") {
            WorkingDirectory = root, RedirectStandardInput = true, RedirectStandardOutput = true,
            RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true
        };
        using Process process = Process.Start(start)!;
        await process.StandardInput.WriteAsync(JsonSerializer.Serialize(values)); process.StandardInput.Close();
        string output = await process.StandardOutput.ReadToEndAsync(token);
        string error = await process.StandardError.ReadToEndAsync(token);
        await process.WaitForExitAsync(token);
        if (process.ExitCode != 0 || string.IsNullOrWhiteSpace(output)) return StatusCode(503, new { error = error.Trim() });
        using JsonDocument document = JsonDocument.Parse(output);
        if (!document.RootElement.GetProperty("ok").GetBoolean()) return BadRequest(new { error = document.RootElement.GetProperty("error").GetString() });
        return Content(document.RootElement.GetProperty("result").GetRawText(), "application/json");
    }

    private async Task<bool> IsHeadStaff(CancellationToken token)
    {
        string id = User.FindFirstValue(ClaimTypes.NameIdentifier)?.Trim() ?? "";
        if (id == "323802380007112704") return true;
        string path=Path.Combine(_environment.ContentRootPath,"Config","mesozoic-ai-admins.json");
        if (!System.IO.File.Exists(path)) return false;
        using JsonDocument doc=JsonDocument.Parse(await System.IO.File.ReadAllTextAsync(path,token));
        return doc.RootElement.TryGetProperty("discordIds",out var ids) && ids.EnumerateArray().Any(x=>x.GetString()==id);
    }
}
