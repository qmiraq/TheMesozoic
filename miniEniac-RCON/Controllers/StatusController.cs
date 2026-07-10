using Microsoft.AspNetCore.Mvc;

namespace miniEniac_RCON.Controllers;

[ApiController]
[Route("[controller]")]
public class StatusController : ControllerBase
{
    [HttpGet] 
    public IActionResult Get()
    {
        return Ok(new
        {
            service = "miniEniac-RCON",
            status = "online"
        });
    }
}