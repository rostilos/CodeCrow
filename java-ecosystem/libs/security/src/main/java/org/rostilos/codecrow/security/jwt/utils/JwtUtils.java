package org.rostilos.codecrow.security.jwt.utils;

import java.security.Key;
import java.util.Date;

import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Component;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.MalformedJwtException;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.UnsupportedJwtException;
import io.jsonwebtoken.io.Decoders;
import io.jsonwebtoken.security.Keys;

@Component
public class JwtUtils {
    private static final Logger logger = LoggerFactory.getLogger(JwtUtils.class);
    private static final int TEMP_TOKEN_EXPIRATION_MS = 300000;

    @Value("${codecrow.security.jwtSecret}")
    private String jwtSecret;

    @Value("${codecrow.security.jwtExpirationMs}")
    private int jwtExpirationMs;

    @Value("${codecrow.security.refreshTokenExpirationMs:604800000}")
    private long refreshTokenExpirationMs;

    @Value("${codecrow.security.projectJwtExpirationMs}")
    private Long projectJwtExpirationMs;

    public String generateJwtToken(Authentication authentication) {
        UserDetailsImpl userPrincipal = (UserDetailsImpl) authentication.getPrincipal();

        return Jwts.builder()
                .setSubject((userPrincipal.getUsername()))
                .setIssuedAt(new Date())
                .setExpiration(new Date((new Date()).getTime() + jwtExpirationMs))
                .signWith(key(), SignatureAlgorithm.HS256)
                .compact();
    }

    /**
     * Generate a JWT token directly for a user (without Authentication object)
     */
    public String generateJwtTokenForUser(Long userId, String username) {
        return Jwts.builder()
                .setSubject(username)
                .claim("userId", userId)
                .setIssuedAt(new Date())
                .setExpiration(new Date((new Date()).getTime() + jwtExpirationMs))
                .signWith(key(), SignatureAlgorithm.HS256)
                .compact();
    }

    public String generateRefreshToken(Long userId, String username) {
        return Jwts.builder()
                .setSubject(username)
                .claim("userId", userId)
                .claim("type", "refresh")
                .setIssuedAt(new Date())
                .setExpiration(new Date((new Date()).getTime() + refreshTokenExpirationMs))
                .signWith(key(), SignatureAlgorithm.HS256)
                .compact();
    }

    public Long getUserIdFromRefreshToken(String token) {
        Claims claims = Jwts.parserBuilder().setSigningKey(key()).build()
                .parseClaimsJws(token).getBody();
        String type = claims.get("type", String.class);
        if (!"refresh".equals(type)) {
            throw new IllegalArgumentException("Invalid refresh token type");
        }
        return claims.get("userId", Long.class);
    }

    public boolean validateRefreshToken(String token) {
        try {
            Claims claims = Jwts.parserBuilder().setSigningKey(key()).build()
                    .parseClaimsJws(token).getBody();
            String type = claims.get("type", String.class);
            return "refresh".equals(type);
        } catch (Exception e) {
            logger.error("Invalid refresh token: {}", e.getMessage());
            return false;
        }
    }

    public long getRefreshTokenExpirationMs() {
        return refreshTokenExpirationMs;
    }

    /**
     * Generate a temporary token for 2FA verification
     */
    public String generateTempToken(Long userId, String username) {
        return Jwts.builder()
                .setSubject(username)
                .claim("userId", userId)
                .claim("type", "2fa_temp")
                .setIssuedAt(new Date())
                .setExpiration(new Date((new Date()).getTime() + TEMP_TOKEN_EXPIRATION_MS))
                .signWith(key(), SignatureAlgorithm.HS256)
                .compact();
    }

    /**
     * Validate a temporary 2FA token
     */
    public boolean validateTempToken(String token) {
        try {
            Claims claims = Jwts.parserBuilder().setSigningKey(key()).build()
                    .parseClaimsJws(token).getBody();
            String type = claims.get("type", String.class);
            return "2fa_temp".equals(type);
        } catch (Exception e) {
            logger.error("Invalid temp token: {}", e.getMessage());
            return false;
        }
    }

    public Long getUserIdFromTempToken(String token) {
        Claims claims = Jwts.parserBuilder().setSigningKey(key()).build()
                .parseClaimsJws(token).getBody();
        return claims.get("userId", Long.class);
    }

    public String getUsernameFromTempToken(String token) {
        return Jwts.parserBuilder().setSigningKey(key()).build()
                .parseClaimsJws(token).getBody().getSubject();
    }

    /**
     * Generate a JWT for a project and embed the requesting user id as a claim.
     * Subject = projectId, claim "userId" = userId
     */
    public String generateJwtTokenForProjectWithUser(String projectId, String userId, Date expires) {
        return Jwts.builder()
                .setSubject(projectId)
                .claim("userId", userId)
                .setIssuedAt(new Date())
                .setExpiration(expires)
                .signWith(key(), SignatureAlgorithm.HS256)
                .compact();
    }

    private Key key() {
        return Keys.hmacShaKeyFor(Decoders.BASE64.decode(jwtSecret));
    }

    public String getUserNameFromJwtToken(String token) {
        return Jwts.parserBuilder().setSigningKey(key()).build()
                .parseClaimsJws(token).getBody().getSubject();
    }

    public boolean validateJwtToken(String authToken) {
        try {
            Jwts.parserBuilder().setSigningKey(key()).build().parse(authToken);
            return true;
        } catch (MalformedJwtException e) {
            logger.error("Invalid JWT token: {}", e.getMessage());
        } catch (ExpiredJwtException e) {
            logger.error("JWT token is expired: {}", e.getMessage());
        } catch (UnsupportedJwtException e) {
            logger.error("JWT token is unsupported: {}", e.getMessage());
        } catch (IllegalArgumentException e) {
            logger.error("JWT claims string is empty: {}", e.getMessage());
        }

        return false;
    }
}
