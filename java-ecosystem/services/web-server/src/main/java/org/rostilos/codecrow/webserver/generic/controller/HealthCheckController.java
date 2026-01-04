package org.rostilos.codecrow.webserver.generic.controller;

import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/actuator")
public class HealthCheckController {
    @GetMapping("/health")
    public String health() {
        return "OK";
    }
}
